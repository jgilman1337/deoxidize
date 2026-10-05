"""APT preferences (pin file) rendering and writing."""

from __future__ import annotations

import os
from pathlib import Path

from .model import Deoxidizer


def render_pin_file(deoxidizer: Deoxidizer, include_deferred: bool = True) -> str:
	"""Render the APT preferences file body for one deoxidizer.

	Early-phase writes must exclude deferred packages: pinning a blocked
	provider before the swap breaks APT's provider resolution.
	"""
	# Header names the source definition so admins can trace the pin.
	lines = [
		f"# Managed by deoxidize (from {deoxidizer.path.name}).",
		"# Pin-Priority -1 means APT will never install these packages.",
		"# Remove this file (and re-run apt update) to undo.",
		"",
	]
	# Collect only the packages allowed in this phase's file.
	pkgs = list(deoxidizer.early_block.packages)
	if include_deferred and deoxidizer.post_swap_block:
		pkgs.extend(deoxidizer.post_swap_block.packages)
	# Every blocked package gets its own stanza; same shape as deoxidize.sh.
	for pkg in pkgs:
		lines.extend(
			[
				f"Package: {pkg}",
				"Pin: release *",
				"Pin-Priority: -1",
				"",
			]
		)
	return "\n".join(lines)


def write_pin_files(deoxidizers: list[Deoxidizer], pref_dir: Path, dry_run: bool = False, full: bool = False) -> None:
	"""Write (or rewrite) one pin file per deoxidizer with a safe umask.

	full=False renders early-phase pins only; full=True adds deferred pins.
	"""
	# Dry-run prints the exact content that would land on disk, writes nothing.
	if dry_run:
		for d in deoxidizers:
			target = pref_dir / f"99-deoxidize-{d.name}.pref"
			print(f"[dry-run] would write {target}:")
			for line in render_pin_file(d, include_deferred=full).splitlines():
				print(f"[dry-run]   {line}")
		return
	# Match deoxidize.sh: world-readable pins, no surprises.
	old_umask = os.umask(0o022)
	try:
		for d in deoxidizers:
			pref_dir.mkdir(parents=True, exist_ok=True)
			target = pref_dir / f"99-deoxidize-{d.name}.pref"
			content = render_pin_file(d, include_deferred=full)
			target.write_text(content, encoding="utf-8")
			print(f"wrote {target}")
	finally:
		os.umask(old_umask)
