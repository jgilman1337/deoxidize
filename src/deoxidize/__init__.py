"""Deoxidize: opt out of Rust replacements on Debian/Ubuntu-style systems.

Data-driven successor to legacy/deoxidize.sh. Each "deoxidizer" is a TOML
file describing a Rust package (or package stack) to block, its GNU/legacy
replacement, and how to verify the result. The engine contains no package
names; all policy lives in deoxidizers/*.toml.

Protection is APT Pin-Priority -1 (blocked packages show Candidate: (none)).
Pins are staged: "early" pins are written before any package surgery,
"post_swap" pins only after replacements are in place (see the coreutils
deoxidizer for why the uutils pin must be deferred).

Run from a session where you still have root (console, su, or working sudo).
Do not run over your only SSH link if you are unsure about sudo removal.

Package layout:
- model: dataclasses for the deoxidizer configuration model
- config: TOML loading/validation and --only/--skip selection
- pins: APT preferences rendering and writing
- system: apt/dpkg subprocess plumbing with dry-run support
- runbook: the staged execution plan (the former step 1-8 sequence)
- cli: argument parsing and entry point
"""

from .model import Block, DeoxidizeError, Deoxidizer, Swap, VerifyTest

__all__ = [
	"Block",
	"DeoxidizeError",
	"Deoxidizer",
	"Swap",
	"VerifyTest",
]
