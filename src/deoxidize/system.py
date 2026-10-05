"""apt/dpkg subprocess plumbing with dry-run support."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .model import DeoxidizeError

# Pin file the legacy bash script wrote; warn when it still exists.
LEGACY_PREF_FILE = "/etc/apt/preferences.d/99-block-sudo-rs-rust-coreutils.pref"


class System:
	"""Root-checked wrapper around apt/dpkg with dry-run support."""

	def __init__(self, dry_run: bool, allow_remove_essential: bool, verbose: bool = False) -> None:
		self.dry_run = dry_run
		self.allow_remove_essential = allow_remove_essential
		# Verbose runs stream even quiet-marked command output (apt update).
		self.verbose = verbose
		# Shared dpkg flags keep existing conffiles instead of prompting.
		self.dpkg_opts = [
			"-o",
			"Dpkg::Options::=--force-confdef",
			"-o",
			"Dpkg::Options::=--force-confold",
		]

	def _announce(self, cmd: list[str]) -> None:
		"""Print a command; raise instead of running when in dry-run mode."""
		print(f"[dry-run] would run: {shlex.join(cmd)}")

	def vprint(self, message: str) -> None:
		"""Print detail output: only on -v, or always in dry-run previews.

		Dry-run's job is showing exactly what would happen, so plan
		previews keep full detail regardless of the verbose flag.
		"""
		if self.verbose or self.dry_run:
			print(message)

	def run(self, cmd: list[str], allow_fail: bool = False) -> bool:
		"""Run a system command, honoring dry-run. Returns success.

		Verbose (-v) streams command output live; normal runs capture it
		and print only on failure, keeping logs to the bare minimum.
		"""
		# Dry-run announces and reports success without touching the system.
		if self.dry_run:
			self._announce(cmd)
			return True
		# Verbose streams live; default captures and shows only failures.
		if self.verbose:
			result = subprocess.run(cmd, check=False)
		else:
			result = subprocess.run(cmd, capture_output=True, text=True, check=False)
			if result.returncode != 0:
				print(result.stdout, end="")
				print(result.stderr, end="", file=sys.stderr)
		# allow_fail callers (verification, unhold) handle failure themselves.
		if result.returncode != 0 and not allow_fail:
			raise DeoxidizeError(f"command failed ({result.returncode}): {shlex.join(cmd)}")
		return result.returncode == 0

	def apt_get(self, *args: str, allow_fail: bool = False) -> bool:
		"""Run apt-get with the shared noninteractive dpkg options."""
		return self.run(["apt-get", "-y", *self.dpkg_opts, *args], allow_fail=allow_fail)

	def apt_install(self, *pkgs: str) -> bool:
		"""Install packages without recommends; prefers apt(8) if present."""
		# apt(8) gives nicer progress on interactive runs; apt-get is the fallback.
		frontend = "apt" if shutil.which("apt") else "apt-get"
		return self.run([frontend, "-y", *self.dpkg_opts, "install", "--no-install-recommends", *pkgs])

	def swap(self, install: list[str], remove: list[str], essential: bool) -> bool:
		"""Install replacements and remove targets in one apt transaction."""
		# Trailing "-" on a package name means "remove it" in apt(8) syntax;
		# doing both in one transaction avoids "two conflicting assignments".
		pkgs = list(install) + [f"{p}-" for p in remove]
		cmd = ["apt", "-y", *self.dpkg_opts, "install", "--no-install-recommends"]
		# Removing an Essential package requires an explicit opt-in.
		if essential:
			if not self.allow_remove_essential:
				raise DeoxidizeError(
					"swap needs to remove an Essential package; re-run with --allow-remove-essential or fix the system manually"
				)
			cmd.append("--allow-remove-essential")
		cmd.extend(pkgs)
		return self.run(cmd)

	def package_installed(self, pkg: str) -> bool:
		"""True if dpkg reports the package as 'ok installed'."""
		# Dry-run assumes worst case (everything installed) so the printed
		# plan shows the full swap/removal a dirty system would need.
		if self.dry_run:
			return True
		result = subprocess.run(
			["dpkg-query", "-W", "-f=${Status}", pkg],
			capture_output=True,
			text=True,
			check=False,
		)
		return "ok installed" in result.stdout

	def set_alternative(self, name: str, path: str) -> bool:
		"""Point an update-alternatives master link at a registered path.

		Best-effort by design: releases without the alternatives group, or
		without the path registered in it, are skipped with a note instead
		of failing the run — alternatives wiring varies across Ubuntu
		versions and the verify tests still catch a wrong binary on PATH.
		"""
		# --list is read-only, so probing is safe even in dry-run: the plan
		# preview shows what a real run would decide on this system.
		probe = subprocess.run(
			["update-alternatives", "--list", name],
			capture_output=True,
			text=True,
			check=False,
		)
		# A missing group (e.g. a binary not alternatives-managed) is not an error.
		if probe.returncode != 0:
			print(f"note: no update-alternatives group {name!r}; nothing to select")
			return True
		registered = probe.stdout.split()
		# --set requires the exact registered path; anything else is skipped.
		if path not in registered:
			choices = ", ".join(registered) or "(none)"
			print(f"note: {path} is not registered in alternatives group {name!r} (choices: {choices}); skipping")
			return True
		return self.run(["update-alternatives", "--set", name, path])


def deoxidizers_dir_default(start: Path) -> Path:
	"""Default deoxidizers/ location: repo root, relative to a src file."""
	# start is typically __file__ of cli.py: src/deoxidize/cli.py.
	return start.resolve().parents[2] / "deoxidizers"
