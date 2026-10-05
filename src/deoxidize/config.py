"""TOML loading, validation, and --only/--skip selection of deoxidizers."""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

from .model import Block, DeoxidizeError, Deoxidizer, Swap, VerifyTest

VALID_PIN_PHASES = ("early", "post_swap")


def _parse_block(table: dict, path: Path) -> Block:
	"""Parse and validate a [[block]]-style table into a Block."""
	# Both fields are required; pin_phase is checked against the allowed set.
	if "packages" not in table or "pin_phase" not in table:
		raise DeoxidizeError(f"{path}: block needs 'packages' and 'pin_phase'")
	block = Block(packages=list(table["packages"]), pin_phase=table["pin_phase"])
	if block.pin_phase not in VALID_PIN_PHASES:
		raise DeoxidizeError(f"{path}: unknown pin_phase {block.pin_phase!r} (expected one of {', '.join(VALID_PIN_PHASES)})")
	if not block.packages:
		raise DeoxidizeError(f"{path}: block.packages must not be empty")
	return block


def _parse_swap(table: dict, path: Path) -> Swap:
	"""Parse and validate the optional [swap] table into a Swap."""
	# A swap without anything to install is a configuration mistake.
	if not table.get("install"):
		raise DeoxidizeError(f"{path}: [swap] needs a non-empty 'install' list")
	return Swap(
		install=list(table["install"]),
		remove=list(table.get("remove", [])),
		essential=bool(table.get("essential", False)),
		fallback=list(table.get("fallback", [])),
	)


def _parse_verify_tests(tables: list[dict], path: Path) -> list[VerifyTest]:
	"""Parse and validate [[verify.tests]] entries into compiled VerifyTests."""
	tests: list[VerifyTest] = []
	for i, table in enumerate(tables):
		# Both fields are required; anything else is a config error up front.
		if "command" not in table or "expected" not in table:
			raise DeoxidizeError(f"{path}: verify.tests[{i}] needs 'command' and 'expected'")
		# Split the command line now (no shell interpolation at run time);
		# bad quoting is a config error, not a runtime surprise.
		try:
			command = shlex.split(str(table["command"]))
		except ValueError as exc:
			raise DeoxidizeError(f"{path}: verify.tests[{i}].command: {exc}") from exc
		if not command:
			raise DeoxidizeError(f"{path}: verify.tests[{i}].command is empty")
		# Compile with MULTILINE|DOTALL: ^/$ anchor per line and patterns may
		# span lines (TOML triple-quoted strings make this readable).
		try:
			expected = re.compile(str(table["expected"]), re.MULTILINE | re.DOTALL)
		except re.error as exc:
			raise DeoxidizeError(f"{path}: verify.tests[{i}].expected: bad regex: {exc}") from exc
		tests.append(VerifyTest(command=command, expected=expected))
	return tests


def load_deoxidizer(path: Path) -> Deoxidizer:
	"""Load one deoxidizer TOML file, validating structure and required keys."""
	# Read + parse the TOML first so syntax errors point at the file.
	try:
		with open(path, "rb") as fh:
			data = tomllib.load(fh)
	except tomllib.TOMLDecodeError as exc:
		raise DeoxidizeError(f"{path}: invalid TOML: {exc}") from exc

	# [meta] carries the identity; name is required and must be filename-safe.
	meta = data.get("meta", {})
	name = meta.get("name")
	if not name or not str(name).replace("-", "").isalnum():
		raise DeoxidizeError(f"{path}: [meta] name is required and must be alphanumeric/dashes")

	# [block] is required; split entries by phase so the engine can stage pins.
	if "block" not in data:
		raise DeoxidizeError(f"{path}: missing required [block] table")
	block = _parse_block(data["block"], path)

	# Early and post_swap blocks live in separate sub-tables when both exist.
	# A flat [block] with pin_phase="early" is the common single-phase case,
	# and an optional [block.deferred] sub-table adds a deferred pin.
	early_block: Block
	post_swap_block: Block | None = None
	if block.pin_phase == "early":
		early_block = block
		if "deferred" in data["block"]:
			post_swap_block = _parse_block(data["block"]["deferred"], path)
	else:
		post_swap_block = block
		early_block = Block(packages=[], pin_phase="early")

	# [swap] and [verify] are optional.
	swap = _parse_swap(data["swap"], path) if "swap" in data else None
	verify = data.get("verify", {})
	binaries = list(verify.get("binaries", []))
	tests = _parse_verify_tests(verify.get("tests", []), path)

	# A block-only deoxidizer must block something in at least one phase.
	if not swap and not post_swap_block and not early_block.packages:
		raise DeoxidizeError(f"{path}: deoxidizer blocks nothing and swaps nothing")

	return Deoxidizer(
		name=str(name),
		description=str(meta.get("description", "")),
		early_block=early_block,
		post_swap_block=post_swap_block,
		swap=swap,
		verify_binaries=binaries,
		verify_tests=tests,
		path=path,
	)


def load_deoxidizers(directory: Path) -> list[Deoxidizer]:
	"""Load every *.toml in the deoxidizers directory, sorted by filename."""
	if not directory.is_dir():
		raise DeoxidizeError(f"deoxidizers directory not found: {directory}")
	# Sorted so pin files and run output are deterministic.
	paths = sorted(directory.glob("*.toml"))
	if not paths:
		raise DeoxidizeError(f"no deoxidizer definitions in {directory}")
	deoxidizers = [load_deoxidizer(p) for p in paths]

	# Names must be unique: they key the pin files.
	seen: set[str] = set()
	for d in deoxidizers:
		if d.name in seen:
			raise DeoxidizeError(f"{d.path}: duplicate deoxidizer name {d.name!r}")
		seen.add(d.name)
	return deoxidizers


def select_deoxidizers(deoxidizers: list[Deoxidizer], only: list[str], skip: list[str]) -> list[Deoxidizer]:
	"""Filter deoxidizers by --only/--skip names, validating names exist.

	All definitions are always loaded and validated first; this only decides
	which ones the runbook executes. --only takes precedence over --skip.
	"""
	# --only wins: run exactly the named set, in definition order.
	if only:
		return _filter_by_name(deoxidizers, only, keep=True)
	if skip:
		return _filter_by_name(deoxidizers, skip, keep=False)
	return deoxidizers


def _filter_by_name(deoxidizers: list[Deoxidizer], names: list[str], keep: bool) -> list[Deoxidizer]:
	"""Keep (or drop) deoxidizers matching names; unknown names are errors."""
	# Unknown names usually mean typos — refuse rather than silently no-op.
	known = {d.name for d in deoxidizers}
	missing = set(names) - known
	if missing:
		raise DeoxidizeError(f"unknown deoxidizer(s): {', '.join(sorted(missing))} (available: {', '.join(sorted(known))})")
	return [d for d in deoxidizers if (d.name in names) == keep]
