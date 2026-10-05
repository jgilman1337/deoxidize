"""The staged runbook: the former deoxidize.sh step 1-8 sequence."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .model import DeoxidizeError, Deoxidizer
from .pins import write_pin_files
from .system import LEGACY_PREF_FILE, System


def apply_alternatives(sys_: System, d: Deoxidizer) -> None:
	"""Re-point the deoxidizer's update-alternatives groups at the GNU side.

	Idempotent and best-effort: the probe in System.set_alternative skips
	groups or paths this system does not have.
	"""
	# Announce even when there is nothing configured, so runs are legible.
	if not d.alternatives:
		return
	for alt in d.alternatives:
		print(f"[{d.name}] update-alternatives --set {alt.name} {alt.apply}")
		sys_.set_alternative(alt.name, alt.apply)


def run_post_install(sys_: System, d: Deoxidizer) -> None:
	"""Run the deoxidizer's declarative post-install commands, best-effort.

	The swap and pins have already succeeded by this point, so an auxiliary
	command failing (e.g. a metapackage restore) warns instead of failing
	the run — the admin sees it and can re-run or fix manually.
	"""
	for post in d.post_install:
		print(f"[{d.name}] post-install: {post.raw}")
		try:
			sys_.run(post.command)
		except DeoxidizeError as exc:
			print(f"warning: post-install command failed: {exc}")


def _install_or_fallback(sys_: System, d: Deoxidizer, install: list[str]) -> None:
	"""Install the replacement packages, falling back when the swap fails."""
	if sys_.apt_install(*install):
		return
	# Primary install failed: try the fallback metapackage before giving up.
	if not d.swap or not d.swap.fallback:
		raise DeoxidizeError(f"[{d.name}] install failed and no fallback is defined")
	print(f"[{d.name}] install failed; installing legacy fallback {', '.join(d.swap.fallback)}")
	sys_.apt_install(*d.swap.fallback)


def apply_staged_swap(sys_: System, d: Deoxidizer, offending: list[str]) -> None:
	"""Staged swap: install replacements, re-point alternatives, then remove.

	The safe ordering for binaries that must never disappear (sudo): the
	replacement is installed and alternatives already select it BEFORE the
	offending package is removed, so no point in the run leaves the link
	dangling or a broken binary on PATH.
	"""
	swap = d.swap
	assert swap is not None
	# 1) Install the replacement side on its own (both providers may briefly
	# coexist; alternatives still picks the old path until step 2).
	_install_or_fallback(sys_, d, swap.install)
	# 2) Re-point alternatives while both sides are registered, so removal
	# below cannot leave the master link pointing at a vanishing binary.
	apply_alternatives(sys_, d)
	# 3) Remove the offending packages in their own transaction.
	if swap.essential:
		sys_.apt_get("remove", "--allow-remove-essential", *offending)
	else:
		sys_.apt_get("remove", *offending)


def apply_same_transaction_swap(sys_: System, d: Deoxidizer, remaining: list[str]) -> None:
	"""Same-transaction swap: install + remove in one apt solve.

	Required when both providers conflict and installing the replacement
	alone can fail with 'two conflicting assignments' while the blocked
	provider stays selected.
	"""
	swap = d.swap
	assert swap is not None
	if not sys_.swap(swap.install, remaining, swap.essential):
		# Primary swap failed: try the fallback metapackage before giving up.
		_install_or_fallback(sys_, d, swap.fallback)


def apply_deoxidizer(sys_: System, d: Deoxidizer) -> None:
	"""Apply one deoxidizer's swap (if any) and remove leftover targets."""
	if not d.swap:
		# Block-only deoxidizers swap nothing, but may still steer
		# alternatives groups (e.g. when the GNU side is already installed).
		apply_alternatives(sys_, d)
		return
	swap = d.swap
	# Skip the swap entirely when the replacement is already installed and
	# nothing blocked remains — the idempotent re-run case.
	remaining = [p for p in swap.remove if sys_.package_installed(p)]
	if not remaining:
		print(f"[{d.name}] nothing to swap; replacement already in place")
		# Re-run safety: still fix alternatives that point at the rust side.
		apply_alternatives(sys_, d)
		return
	mode = "staged swap" if swap.staged else "same-transaction swap"
	print(f"[{d.name}] {mode}: install {', '.join(swap.install)} (removing {', '.join(remaining)})")
	if swap.staged:
		apply_staged_swap(sys_, d, remaining)
	else:
		apply_same_transaction_swap(sys_, d, remaining)

	# Remove any still-installed blocked packages (metapackage transitions).
	leftovers = [p for p in d.blocked_packages if sys_.package_installed(p)]
	if leftovers:
		print(f"[{d.name}] removing leftovers: {', '.join(leftovers)}")
		sys_.apt_get("remove", "--allow-remove-essential", *leftovers) if swap.essential else sys_.apt_get("remove", *leftovers)

	# The swap is done: make sure the alternatives group selects the GNU path.
	apply_alternatives(sys_, d)
	# Auxiliary declarative commands (e.g. metapackage restoration) run last.
	run_post_install(sys_, d)


def verify_deoxidizer(sys_: System, d: Deoxidizer, pref_dir: Path) -> bool:
	"""Print post-run evidence: pin state, apt policy, binary ownership, tests.

	Returns True when every verification passed.
	"""
	print(f"--- [{d.name}] verification ---")
	ok = True

	# Show the pin file content so the admin sees exactly what is enforced.
	if not sys_.dry_run:
		pin = pref_dir / f"99-deoxidize-{d.name}.pref"
		if pin.exists():
			print(f"--- {pin} ---")
			print(pin.read_text(encoding="utf-8"), end="")

	# apt-cache policy is the ground truth for whether pins took effect.
	if d.blocked_packages and not sys_.dry_run:
		sys_.run(["apt-cache", "policy", *d.blocked_packages], allow_fail=True)

	# Verify each expected binary exists and is owned by a package.
	for binary in d.verify_binaries:
		# Locate the binary on PATH; note it but do not fail if absent.
		resolved = shutil.which(binary)
		if not resolved:
			print(f"note: {binary} not found on PATH")
			continue
		print(f"{binary}: {resolved}")
		if sys_.dry_run:
			continue
		# dpkg -S identifies the owning package; alternatives symlinks need
		# a resolved-realpath retry (same logic as deoxidize.sh).
		owner = subprocess.run(["dpkg", "-S", resolved], capture_output=True, text=True, check=False)
		if owner.returncode == 0:
			print(owner.stdout.strip())
			continue
		real = Path(resolved).resolve()
		if real != Path(resolved):
			owner = subprocess.run(["dpkg", "-S", str(real)], capture_output=True, text=True, check=False)
			if owner.returncode == 0:
				print(owner.stdout.strip())
				continue
		print(f"(no single deb owns path for {binary}: {resolved}; try: dpkg -L {binary})")

	# Run the output tests: each command's output must match its pattern.
	for t in d.verify_tests:
		label = f"{shlex.join(t.command)} ~ /{t.pattern}/"
		# Dry-run announces the test without executing anything.
		if sys_.dry_run:
			print(f"[dry-run] would run: {label}")
			continue
		result = subprocess.run(t.command, capture_output=True, text=True, check=False)
		output = result.stdout + result.stderr
		# A crashing command cannot satisfy its pattern: fail with context.
		if result.returncode != 0:
			print(f"FAIL: {label} (exit {result.returncode})")
			ok = False
			continue
		# Pattern match on the combined output decides pass/fail.
		if t.expected.search(output):
			print(f"PASS: {label}")
		else:
			print(f"FAIL: {label}")
			# Show a short head of actual output to ease debugging.
			head = " | ".join(output.splitlines()[:3])
			print(f"      output head: {head if head else '(no output)'}")
			ok = False
	return ok


def run_plan(sys_: System, deoxidizers: list[Deoxidizer], autoremove: bool, pref_dir: Path) -> bool:
	"""Execute the full staged runbook, mirroring deoxidize.sh's ordering."""
	# Warn about the legacy bash-era pin file so undo instructions stay sane.
	if not sys_.dry_run and Path(LEGACY_PREF_FILE).exists():
		print(f"note: legacy pin file {LEGACY_PREF_FILE} exists; consider removing it")

	print("=== 1) APT preferences (early pins; deferred pins come later) ===")
	early = [d for d in deoxidizers if d.early_block.packages]
	write_pin_files(early, pref_dir, dry_run=sys_.dry_run, full=False)

	print("=== 2) Refresh package lists ===")
	sys_.apt_get("update")

	print("=== 3) Ensure declared bootstrap packages are present before surgery ===")
	# Union of every deoxidizer's [swap] ensure list, in definition order.
	ensure: list[str] = []
	for d in deoxidizers:
		if d.swap:
			for pkg in d.swap.ensure:
				if pkg not in ensure:
					ensure.append(pkg)
	if not ensure:
		print("no bootstrap packages declared; skipping")
	else:
		# Real runs skip packages already installed; dry-run shows the full
		# list so the preview stays complete (worst-case assumption).
		needed = list(ensure) if sys_.dry_run else [p for p in ensure if not sys_.package_installed(p)]
		if needed:
			sys_.apt_install(*needed)
		else:
			print("bootstrap packages already present")

	print("=== 4) Apply swaps and remove Rust replacements ===")
	for d in deoxidizers:
		apply_deoxidizer(sys_, d)

	print("=== 4b) Full APT preferences (deferred pins; safe after swaps) ===")
	write_pin_files(deoxidizers, pref_dir, dry_run=sys_.dry_run, full=True)
	sys_.apt_get("update")

	print("=== 5) Autoremove unused deps (opt-in) ===")
	if autoremove:
		# Old kernel headers/modules often show up here; safe but alarming.
		sys_.apt_get("autoremove")
	else:
		print("Skipping autoremove (default). Enable with --autoremove or AUTOREMOVE=1.")

	print("=== 6) Upgrade (GNU packages remain candidates) ===")
	sys_.apt_get("full-upgrade")

	print("=== 7) Ensure replacements are not on hold ===")
	# Pins replace holds; unholding replacements is cleanup, best-effort.
	if not sys_.dry_run:
		for d in deoxidizers:
			if d.replacement_packages:
				sys_.run(["apt-mark", "unhold", *d.replacement_packages], allow_fail=True)

	print("=== 8) Verify ===")
	all_ok = True
	for d in deoxidizers:
		if not verify_deoxidizer(sys_, d, pref_dir):
			all_ok = False
	# Verification failures surface in the exit code for automation.
	if not all_ok:
		print("error: one or more verifications FAILED", file=sys.stderr)

	print("done. To allow the blocked stacks again: rm -f /etc/apt/preferences.d/99-deoxidize-*.pref && apt-get update")
	return all_ok
