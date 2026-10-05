"""Deoxidizer configuration model: dataclasses only, no I/O or policy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class DeoxidizeError(Exception):
	"""Fatal, user-facing error (bad config, failed command, missing root)."""


@dataclass
class Block:
	"""A set of packages to pin out of the archive at a given phase."""

	# Package names to pin with Pin-Priority -1.
	packages: list[str]
	# When this block takes effect: "early" (before surgery) or "post_swap".
	pin_phase: str


@dataclass
class Swap:
	"""A single-transaction replacement of blocked packages."""

	# Packages to install (the replacement stack).
	install: list[str]
	# Packages to remove in the same transaction (trailing "-" semantics).
	remove: list[str] = field(default_factory=list)
	# True when removal requires --allow-remove-essential (uutils is Essential).
	essential: bool = False
	# Fallback packages (e.g. legacy metapackage) if the primary install fails.
	fallback: list[str] = field(default_factory=list)
	# Packages to make sure are installed BEFORE any surgery (bootstrapping
	# safety net, e.g. keep a root shell working while swaps run).
	ensure: list[str] = field(default_factory=list)
	# True: install replacements, re-point alternatives, then remove targets
	# in separate transactions. False (default): one same-transaction solve.
	staged: bool = False


@dataclass
class VerifyTest:
	"""A command whose output must match an expected regex to pass verify."""

	# Command to run, as an argument list (no shell interpolation).
	command: list[str]
	# Compiled pattern; MULTILINE|DOTALL so ^/$ anchor per line and patterns
	# may span lines. TOML-side it can be a triple-quoted multiline string.
	expected: re.Pattern[str]

	@property
	def pattern(self) -> str:
		"""The raw pattern string, for display."""
		return self.expected.pattern


@dataclass
class Alternative:
	"""An update-alternatives master link to steer toward the GNU side."""

	# Master link name (the symlink update-alternatives manages).
	name: str
	# Registered path to select with --set when applying (the GNU binary).
	apply: str
	# Registered path to select on rollback; None leaves the group alone.
	rollback: str | None = None


@dataclass
class PostInstall:
	"""A command to run after the deoxidizer's apply steps finish."""

	# Command argument list, already split (no shell interpolation).
	command: list[str]
	# Raw command string, for display and --list output.
	raw: str


@dataclass
class Deoxidizer:
	"""One declarative de-Rusting definition loaded from TOML."""

	# Unique short name; also used for the pin file suffix.
	name: str
	# Human-readable description of what this deoxidizer defends.
	description: str
	# Packages to block, split by pin phase.
	early_block: Block
	post_swap_block: Block | None
	# Optional replacement swap; None means block-only.
	swap: Swap | None
	# Binaries to verify at the end (command -v + dpkg -S ownership).
	verify_binaries: list[str]
	# Output tests to run at the end (command output must match regex).
	verify_tests: list[VerifyTest] = field(default_factory=list)
	# update-alternatives groups to re-point after the swap (and on rollback).
	alternatives: list[Alternative] = field(default_factory=list)
	# Commands to run after the apply steps finish (best-effort).
	post_install: list[PostInstall] = field(default_factory=list)
	# Source path, for error messages and --list output.
	path: Path = field(default_factory=Path)

	@property
	def blocked_packages(self) -> list[str]:
		"""All blocked package names across both pin phases."""
		pkgs = list(self.early_block.packages)
		if self.post_swap_block:
			pkgs.extend(self.post_swap_block.packages)
		return pkgs

	@property
	def replacement_packages(self) -> list[str]:
		"""Packages the swap installs, including fallbacks (for unhold)."""
		if not self.swap:
			return []
		return list(self.swap.install) + list(self.swap.fallback)
