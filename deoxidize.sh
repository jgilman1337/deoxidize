#!/usr/bin/env bash
# Opt out of sudo-rs and Rust/uutils coreutils on Debian/Ubuntu-style systems.
# Protection is APT Pin-Priority -1 (blocked packages show Candidate: (none)).
# apt-mark hold cannot apply to those names on current apt when no candidate exists.
#
# Run from a session where you still have root (console, su, or working sudo).
# Do not run over your only SSH link if you are unsure about sudo removal.
#
# Pinning coreutils-from-uutils to -1 *before* swapping to coreutils-from-gnu can
# confuse APT into selecting both providers (impossible Conflicts). We pin only
# sudo-rs + rust-coreutils first, swap coreutils, then write the uutils pin.
# Step 4 may need --allow-remove-essential; set ALLOW_REMOVE_ESSENTIAL=0 to abort.
# Autoremove is OFF by default: it often lists old linux-headers/linux-modules for a
# kernel you no longer boot (safe but looks terrifying). Set AUTOREMOVE=1 to run it.
set -euo pipefail

PREF_FILE="/etc/apt/preferences.d/99-block-sudo-rs-rust-coreutils.pref"
DRY_RUN="${DRY_RUN:-0}"
ALLOW_REMOVE_ESSENTIAL="${ALLOW_REMOVE_ESSENTIAL:-1}"
AUTOREMOVE="${AUTOREMOVE:-0}"

die() { echo "error: $*" >&2; exit 1; }

if [[ "$(id -u)" -ne 0 ]]; then
	die "run as root: sudo $0"
fi

export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"

# Before coreutils swap: do not pin coreutils-from-uutils (see header).
write_preferences_early() {
	umask 022
	cat >"$PREF_FILE" <<'EOF'
# Block Rust replacement packages from being selected by APT.
# Pin-Priority -1 means "never install".
# (coreutils-from-uutils is pinned after GNU coreutils is in place — see script.)

Package: sudo-rs
Pin: release *
Pin-Priority: -1

Package: rust-coreutils
Pin: release *
Pin-Priority: -1
EOF
	echo "wrote $PREF_FILE (early: sudo-rs, rust-coreutils)"
}

write_preferences_full() {
	umask 022
	cat >"$PREF_FILE" <<'EOF'
# Block Rust replacement packages from being selected by APT.
# Pin-Priority -1 means "never install".

Package: sudo-rs
Pin: release *
Pin-Priority: -1

Package: rust-coreutils
Pin: release *
Pin-Priority: -1

Package: coreutils-from-uutils
Pin: release *
Pin-Priority: -1
EOF
	echo "wrote $PREF_FILE (full: includes coreutils-from-uutils)"
}

apt_wrap() {
	if [[ "$DRY_RUN" == "1" ]]; then
		echo "[dry-run] would run: apt-get $*"
		return 0
	fi
	apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}

apt_wrap_allow_remove_essential() {
	if [[ "$DRY_RUN" == "1" ]]; then
		echo "[dry-run] would run: apt-get (allow-remove-essential) $*"
		return 0
	fi
	if [[ "$ALLOW_REMOVE_ESSENTIAL" != "1" ]]; then
		die "apt needs to remove essential metapackages for this step; re-run with ALLOW_REMOVE_ESSENTIAL=1 or fix the system manually"
	fi
	apt-get -y --allow-remove-essential \
		-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}

coreutils_from_gnu_available() {
	apt-cache show coreutils-from-gnu >/dev/null 2>&1
}

# Plain `apt-get install coreutils-from-gnu` can fail with "two conflicting assignments"
# (gnu and uutils both satisfy coreutils-from). Removing uutils in the *same* transaction fixes it.
# Trailing `-` is apt(8) "remove this package". `coreutils-from-uutils` is Essential=yes on Ubuntu,
# so the swap needs `--allow-remove-essential` (same as step 4) or apt aborts before applying.
install_coreutils_from_gnu_swap() {
	local -a pkgs=(coreutils-from-gnu)
	local removing_uutils=false
	if dpkg-query -W -f='${Status}' coreutils-from-uutils 2>/dev/null | grep -q "ok installed"; then
		pkgs+=(coreutils-from-uutils-)
		removing_uutils=true
	fi
	if [[ "$DRY_RUN" == "1" ]]; then
		local flags=""
		[[ "$removing_uutils" == true ]] && flags=" --allow-remove-essential"
		echo "[dry-run] would swap to GNU: apt -y${flags} … install --no-install-recommends ${pkgs[*]}"
		return 0
	fi
	local -a common=( -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold )
	if [[ "$removing_uutils" == true ]]; then
		if [[ "$ALLOW_REMOVE_ESSENTIAL" != "1" ]]; then
			die "swapping off coreutils-from-uutils needs ALLOW_REMOVE_ESSENTIAL=1 (Essential package)"
		fi
		common+=(--allow-remove-essential)
	fi
	common+=(install --no-install-recommends)
	if command -v apt >/dev/null 2>&1; then
		apt "${common[@]}" "${pkgs[@]}"
	else
		apt-get "${common[@]}" "${pkgs[@]}"
	fi
}

dpkg_owner_or_note() {
	local bin="$1"
	local p real
	p="$(command -v "$bin" 2>/dev/null || true)"
	[[ -z "$p" ]] && return 0
	if dpkg -S "$p" 2>/dev/null; then
		return 0
	fi
	real="$(readlink -f "$p" 2>/dev/null || true)"
	if [[ -n "$real" && "$real" != "$p" ]] && dpkg -S "$real" 2>/dev/null; then
		return 0
	fi
	echo "(no single deb owns path for $bin: $p${real:+ -> $real}; try: dpkg -L sudo | head)"
}

echo "=== 1) APT preferences (blacklist; uutils pin deferred) ==="
write_preferences_early

echo "=== 2) Refresh package lists ==="
apt_wrap update

echo "=== 3) Ensure GNU sudo and GNU coreutils provider ==="
apt_wrap install --no-install-recommends sudo

if coreutils_from_gnu_available; then
	if ! dpkg-query -W -f='${Status}' coreutils-from-gnu 2>/dev/null | grep -q "ok installed"; then
		if ! install_coreutils_from_gnu_swap; then
			echo "note: GNU coreutils swap failed; installing legacy coreutils metapackage"
			apt_wrap install --no-install-recommends coreutils
		fi
	fi
else
	echo "note: coreutils-from-gnu not in archive; installing legacy coreutils"
	apt_wrap install --no-install-recommends coreutils
fi

echo "=== 4) Remove Rust replacements (skip if none installed) ==="
rust_pkgs=( )
for pkg in sudo-rs rust-coreutils coreutils-from-uutils; do
	if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
		rust_pkgs+=("$pkg")
	fi
done
if ((${#rust_pkgs[@]})); then
	apt_wrap_allow_remove_essential remove "${rust_pkgs[@]}"
fi

echo "=== 4b) Full APT preferences (pin coreutils-from-uutils; safe after swap/remove) ==="
write_preferences_full
apt_wrap update

echo "=== 5) Autoremove unused deps (skipped by default) ==="
if [[ "$AUTOREMOVE" == "1" ]]; then
	echo "Running apt-get autoremove. Lists often include OLD kernel headers/modules for a version"
	echo "you are not running anymore (compare package version to: $(uname -r))."
	apt_wrap autoremove
else
	echo "Skipping apt-get autoremove (default). Those linux-headers/linux-modules lines are usually"
	echo "orphaned after a kernel upgrade — unrelated to the coreutils/sudo swap."
	echo "To clean them: sudo apt-get autoremove   OR re-run with: AUTOREMOVE=1 $0"
fi

echo "=== 6) Upgrade (GNU packages remain candidates) ==="
apt_wrap full-upgrade

echo "=== 7) Ensure sudo / coreutils-from-gnu are not on hold ==="
if [[ "$DRY_RUN" != "1" ]]; then
	apt-mark unhold sudo coreutils-from-gnu 2>/dev/null || true
fi
echo "(Blocked packages are excluded by $PREF_FILE; apt-mark hold is not used — no install candidate.)"

echo "=== 8) Verify ==="
echo "--- apt-mark showhold (often empty; pins replace holds) ---"
apt-mark showhold || true
echo "--- apt-cache policy ---"
apt-cache policy sudo-rs rust-coreutils coreutils-from-uutils sudo coreutils-from-gnu
echo "--- dpkg: matching packages ---"
dpkg -l 2>/dev/null | grep -E 'sudo-rs|rust-coreutils|coreutils-from-uutils|coreutils-from-gnu|^ii[[:space:]]+sudo[[:space:]]' || true
echo "--- active binaries ---"
command -v sudo || true
command -v ls || true
dpkg_owner_or_note sudo
dpkg_owner_or_note ls

echo "done. To allow rust stack again: rm -f $PREF_FILE && apt-get update"
echo "If ubuntu-minimal was removed: sudo apt install --no-install-recommends ubuntu-minimal"
