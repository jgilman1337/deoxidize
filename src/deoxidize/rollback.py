"""Rollback: undo the selected deoxidizers (drop pins, swap the rust stack back)."""

from __future__ import annotations

from pathlib import Path

from .model import Deoxidizer
from .system import LEGACY_PREF_FILE, System


def run_rollback(sys_: System, deoxidizers: list[Deoxidizer], pref_dir: Path) -> None:
	"""Undo the selected deoxidizers: remove pins, refresh, swap back, show policy.

	Mirrors the apply runbook in reverse: pins must go before APT can see the
	blocked stack again, and the swap back is one same-transaction install+remove.
	"""
	print("=== R1) Remove deoxidize pin files ===")
	# The legacy bash-era file also pins the rust stack out; it must go too
	# or APT would refuse to reinstall the blocked packages.
	legacy = Path(LEGACY_PREF_FILE)
	if legacy.exists():
		# Dry-run only announces the legacy file's removal.
		if sys_.dry_run:
			print(f"[dry-run] would remove legacy pin file: {legacy}")
		else:
			legacy.unlink()
			print(f"removed legacy pin file: {legacy}")
	for d in deoxidizers:
		pin = pref_dir / f"99-deoxidize-{d.name}.pref"
		# Dry-run announces without touching the filesystem.
		if sys_.dry_run:
			print(f"[dry-run] would remove: {pin}")
			continue
		# Missing pin files are fine: rollback is idempotent.
		if pin.exists():
			pin.unlink()
			print(f"removed {pin}")
		else:
			print(f"(no pin file at {pin}; nothing to remove)")

	print("=== R2) Refresh package lists (blocked stack becomes installable again) ===")
	sys_.apt_get("update")

	print("=== R3) Swap back to the previously blocked stack ===")
	for d in deoxidizers:
		# Block-only deoxidizers never removed anything; the pin removal
		# above is the entire rollback for them.
		if not d.swap:
			continue
		# Only GNU replacements actually installed need removing.
		installed = [p for p in d.swap.install if sys_.package_installed(p)]
		if not installed:
			print(f"[{d.name}] nothing to roll back; replacement {', '.join(d.swap.install)} not installed")
			continue
		print(f"[{d.name}] restoring {', '.join(d.blocked_packages)} (removing {', '.join(installed)})")
		# Same-transaction inverse swap. The GNU side is not Essential, so
		# ALLOW_REMOVE_ESSENTIAL deliberately does not gate rollback: that
		# flag guards the forward swap's uutils removal, not this direction.
		sys_.swap(d.blocked_packages, installed, False)
		# Re-point alternatives at the restored (rust) side so master links
		# follow the swap instead of staying on the replacement's path.
		for alt in d.alternatives:
			if alt.rollback:
				print(f"[{d.name}] update-alternatives --set {alt.name} {alt.rollback}")
				sys_.set_alternative(alt.name, alt.rollback)

	print("=== R4) Refresh package lists and show resulting policy ===")
	sys_.apt_get("update")
	# apt-cache policy is the ground truth that the pins are gone.
	for d in deoxidizers:
		if d.blocked_packages and not sys_.dry_run:
			sys_.run(["apt-cache", "policy", *d.blocked_packages], allow_fail=True)
	print("rollback complete; the previously blocked stacks are no longer pinned out")
