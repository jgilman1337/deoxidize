"""Argument parsing and the main entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import load_deoxidizers, select_deoxidizers, select_interactive
from .model import DeoxidizeError
from .preflight import confirm_plan, ensure_up_to_date
from .rollback import run_rollback
from .runbook import run_plan
from .system import System, deoxidizers_dir_default

# Engine exit codes.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def parse_args(argv: list[str]) -> argparse.Namespace:
	"""Parse CLI flags. Environment variables provide backward-compatible defaults."""
	# Env defaults keep deoxidize.sh-compatible invocations working.
	env_dry_run = os.environ.get("DRY_RUN", "0") == "1"
	env_autoremove = os.environ.get("AUTOREMOVE", "0") == "1"
	env_allow_essential = os.environ.get("ALLOW_REMOVE_ESSENTIAL", "1") != "0"

	parser = argparse.ArgumentParser(
		prog="deoxidize",
		description="Prefer GNU/baseline userland packages over their Rust "
		"replacements on Debian/Ubuntu, via staged APT pins. Package policy "
		"lives in declarative TOML deoxidizer definitions.",
	)
	parser.add_argument(
		"-n",
		"--dry-run",
		action="store_true",
		default=env_dry_run,
		help="print planned actions instead of changing anything (env: DRY_RUN=1)",
	)
	parser.add_argument(
		"-a",
		"--autoremove",
		action="store_true",
		default=env_autoremove,
		help="run apt-get autoremove at the end (env: AUTOREMOVE=1)",
	)
	parser.add_argument(
		"--allow-remove-essential",
		action=argparse.BooleanOptionalAction,
		default=env_allow_essential,
		help="permit removal of Essential packages, such as the blocked "
		"provider a swap replaces (env: ALLOW_REMOVE_ESSENTIAL=0 to deny)",
	)
	parser.add_argument(
		"-y",
		"--yes",
		action="store_true",
		help="skip the pre-run confirmation prompt",
	)
	parser.add_argument(
		"-v",
		"--verbose",
		action="store_true",
		help="show detail: stream command output live, print pin bodies, "
		"apt-cache policy, and dpkg -S ownership (normal runs stay minimal)",
	)
	parser.add_argument(
		"--allow-outdated",
		action="store_true",
		help="skip the up-to-date pre-flight check (offline/air-gapped hosts)",
	)
	parser.add_argument(
		"-i",
		"--interactive",
		action="store_true",
		help="interactively pick which deoxidizers to run (needs a TTY; conflicts with --only/--skip)",
	)
	parser.add_argument(
		"-r",
		"--rollback",
		action="store_true",
		help="undo the selected deoxidizers: remove pins and swap the blocked stack back in",
	)
	parser.add_argument(
		"--deoxidizers-dir",
		type=Path,
		default=None,
		help="directory of TOML deoxidizer definitions (default: ./deoxidizers next to this script)",
	)
	parser.add_argument(
		"--pref-dir",
		type=Path,
		default=Path("/etc/apt/preferences.d"),
		help="APT preferences directory for pin files (default: /etc/apt/preferences.d)",
	)
	parser.add_argument(
		"-o",
		"--only",
		action="append",
		default=[],
		metavar="NAME",
		help="deoxidize only these deoxidizers (comma-separated; repeatable)",
	)
	parser.add_argument(
		"--skip",
		action="append",
		default=[],
		metavar="NAME",
		help="deoxidize all except these (comma-separated; repeatable)",
	)
	parser.add_argument(
		"--list",
		action="store_true",
		help="list loaded deoxidizers (after --only/--skip filtering) and exit",
	)
	return parser.parse_args(argv)


def main(argv: list[str]) -> int:
	"""Entry point: load config, dispatch, map errors to exit codes."""
	args = parse_args(argv)

	try:
		# Every definition is loaded and validated, even unselected ones:
		# a broken TOML should abort the run, not hide behind --skip.
		all_deoxidizers = load_deoxidizers(args.deoxidizers_dir or deoxidizers_dir_default(Path(__file__)))
		# Comma-separated values in repeatable flags flatten to name lists.
		only = [n for part in args.only for n in part.split(",") if n]
		skip = [n for part in args.skip for n in part.split(",") if n]
		# Interactive picking cannot coexist with name-based filtering.
		if args.interactive and (only or skip):
			raise DeoxidizeError("-i/--interactive cannot be combined with --only/--skip")
		deoxidizers = select_deoxidizers(all_deoxidizers, only, skip)
	except DeoxidizeError as exc:
		print(f"error: {exc}", file=sys.stderr)
		return EXIT_USAGE

	# --list is a safe, root-free way to inspect the plan (and selection).
	if args.list:
		if not deoxidizers:
			print("no deoxidizers selected")
		for d in deoxidizers:
			print(f"{d.name}: {d.description}")
			# Blocks per phase show the staged-pin plan.
			if d.early_block.packages:
				print(f"  blocks [early]: {', '.join(d.early_block.packages)}")
			if d.post_swap_block:
				print(f"  blocks [post_swap]: {', '.join(d.post_swap_block.packages)}")
			# Swap details include the flags the run will need.
			if d.swap:
				extras: list[str] = []
				if d.swap.essential:
					extras.append("essential removal")
				if d.swap.fallback:
					extras.append(f"fallback: {', '.join(d.swap.fallback)}")
				suffix = f" ({', '.join(extras)})" if extras else ""
				print(f"  swap: install {', '.join(d.swap.install)}{suffix}")
			for alt in d.alternatives:
				line = f"  alternatives: {alt.name} -> {alt.apply}"
				if alt.rollback:
					line += f" (rollback: {alt.rollback})"
				print(line)
			for post in d.post_install:
				print(f"  post-install: {post.raw}")
			if d.verify_binaries or d.verify_tests:
				print(f"  verify: {len(d.verify_binaries)} binary check(s), {len(d.verify_tests)} output test(s)")
			print(f"  source: {d.path}")
		return EXIT_OK

	# Interactive selection happens after --list so listing never prompts.
	if args.interactive:
		try:
			deoxidizers = select_interactive(all_deoxidizers)
		except DeoxidizeError as exc:
			print(f"error: {exc}", file=sys.stderr)
			return EXIT_USAGE

	# Mutating runs require root; dry-run is intentionally root-free.
	if not args.dry_run and os.geteuid() != 0:
		print("error: run as root: sudo ./deoxidize", file=sys.stderr)
		return EXIT_ERROR

	sys_ = System(dry_run=args.dry_run, allow_remove_essential=args.allow_remove_essential, verbose=args.verbose)
	try:
		if args.rollback:
			# Rollback deliberately skips the freshness gate: undo must work
			# even on a broken or offline system.
			if not sys_.dry_run and not args.yes:
				# Preview first: exactly which files are removed and which
				# commands run, before asking for consent.
				print("=== Rollback preview: exactly what will be removed and run ===")
				preview = System(dry_run=True, allow_remove_essential=args.allow_remove_essential, verbose=args.verbose)
				run_rollback(preview, deoxidizers, args.pref_dir)
				print("=== End rollback preview ===")
			confirm_plan(sys_, deoxidizers, assume_yes=args.yes, action="roll back")
			run_rollback(sys_, deoxidizers, args.pref_dir)
			success = True
		else:
			# Freshness gate: a real run aborts unless the system is current.
			if not args.allow_outdated:
				ensure_up_to_date(sys_)
			# Show exactly what will be written and run before asking.
			if not sys_.dry_run and not args.yes:
				# The preview runs the full plan in dry-run mode, which prints
				# every pin file body and every APT command (worst-case
				# assumptions: blocked packages treated as installed).
				print("=== Plan preview: exactly what will be written and run ===")
				preview = System(dry_run=True, allow_remove_essential=args.allow_remove_essential, verbose=args.verbose)
				run_plan(preview, deoxidizers, autoremove=args.autoremove, pref_dir=args.pref_dir)
				print("=== End plan preview ===")
			confirm_plan(sys_, deoxidizers, assume_yes=args.yes, action="apply", autoremove=args.autoremove)
			success = run_plan(sys_, deoxidizers, autoremove=args.autoremove, pref_dir=args.pref_dir)
	except DeoxidizeError as exc:
		print(f"error: {exc}", file=sys.stderr)
		return EXIT_ERROR
	return EXIT_OK if success else EXIT_ERROR
