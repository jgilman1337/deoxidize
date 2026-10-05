"""apt/dpkg subprocess plumbing with dry-run support."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .model import DeoxidizeError

# Pin file the legacy bash script wrote; warn when it still exists.
LEGACY_PREF_FILE = "/etc/apt/preferences.d/99-block-sudo-rs-rust-coreutils.pref"


class System:
	"""Root-checked wrapper around apt/dpkg with dry-run support."""

	def __init__(self, dry_run: bool, allow_remove_essential: bool) -> None:
		self.dry_run = dry_run
		self.allow_remove_essential = allow_remove_essential
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

	def run(self, cmd: list[str], allow_fail: bool = False) -> bool:
		"""Run a system command, honoring dry-run. Returns success."""
		# Dry-run announces and reports success without touching the system.
		if self.dry_run:
			self._announce(cmd)
			return True
		result = subprocess.run(cmd, check=False)
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


def deoxidizers_dir_default(start: Path) -> Path:
	"""Default deoxidizers/ location: repo root, relative to a src file."""
	# start is typically __file__ of cli.py: src/deoxidize/cli.py.
	return start.resolve().parents[2] / "deoxidizers"
