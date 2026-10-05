"""Pre-flight checks run before the plan: freshness gate and run confirmation."""

from __future__ import annotations

import os
import subprocess

from .model import DeoxidizeError, Deoxidizer
from .system import System


def _pending_upgrade_count() -> int:
	"""Count the packages a simulated full-upgrade would touch."""
	# LC_ALL=C keeps apt's simulate tokens stable across locales.
	env = {**os.environ, "LC_ALL": "C"}
	result = subprocess.run(
		["apt-get", "-s", "full-upgrade"],
		capture_output=True,
		text=True,
		check=False,
		env=env,
	)
	# A broken simulation means we cannot know whether the system is current.
	if result.returncode != 0:
		raise DeoxidizeError(f"apt-get -s full-upgrade failed ({result.returncode}): {result.stderr.strip()}")
	# Each simulated 'Inst ' line is one package apt would install or upgrade.
	return sum(1 for line in result.stdout.splitlines() if line.startswith("Inst "))


def ensure_up_to_date(sys_: System) -> None:
	"""Abort the run unless every package is current: refresh lists, then simulate an upgrade."""
	# Fresh package lists make the simulation meaningful; dry-run only announces.
	# Quiet: apt update's hit-list is noise; failures still print their output.
	sys_.apt_get("update", quiet=True)
	pending = _pending_upgrade_count()
	# Dry-run reports without failing so the plan stays previewable.
	if sys_.dry_run:
		if pending:
			print(
				f"[dry-run] note: {pending} package(s) pending upgrade; "
				"a real run would abort (pass --allow-outdated to skip this check)"
			)
		else:
			print("[dry-run] note: system is up to date")
		return
	# A real run must start from a fully upgraded system: swapping userland
	# mid-upgrade invites partial states and pin/upgrade interactions.
	if pending:
		raise DeoxidizeError(
			f"system is not up to date ({pending} package(s) pending upgrade); "
			"run 'sudo apt-get update && sudo apt-get full-upgrade' first, or pass --allow-outdated"
		)
	print("system is up to date (0 pending upgrades)")


def confirm_plan(
	sys_: System,
	deoxidizers: list[Deoxidizer],
	assume_yes: bool,
	action: str = "apply",
	autoremove: bool | None = None,
) -> None:
	"""Summarize the upcoming run and require explicit consent before mutating."""
	# Dry-run changes nothing and --yes is the scripted consent path.
	if sys_.dry_run or assume_yes:
		return
	names = ", ".join(d.name for d in deoxidizers) or "(none)"
	print(f"about to {action} {len(deoxidizers)} deoxidizer(s): {names}")
	# Surface the two most dangerous knobs so consent is informed.
	print(f"  essential removal allowed: {'yes' if sys_.allow_remove_essential else 'no'}")
	# Autoremove only applies to the apply runbook; rollback has no such step.
	if autoremove is not None:
		print(f"  autoremove after upgrade: {'yes' if autoremove else 'no'}")
	# EOF (piped or absent stdin) cannot confirm anything: abort loudly.
	try:
		answer = input("Proceed? [y/N] ")
	except EOFError:
		raise DeoxidizeError("stdin is not interactive; re-run with --yes to skip the confirmation") from None
	# Anything but an explicit yes aborts before any change is made.
	if answer.strip().lower() not in {"y", "yes"}:
		raise DeoxidizeError("aborted; no changes were made")
