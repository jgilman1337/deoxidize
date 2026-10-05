# deoxidize

Prefer **GNU `coreutils` and GNU `sudo`** (and other baselines) on **Debian/Ubuntu-style** systems over the Rust-based stack Ubuntu has been moving toward (`coreutils-from-uutils`, `rust-coreutils`, `sudo-rs`). Implemented as a **stdlib-only Python engine** with **declarative TOML deoxidizer definitions** — the legacy bash script is preserved under **`legacy/`**.

**Quick start:** `sudo ./deoxidize` from a root-capable session (local console or a shell where `sudo` still works). Read the definitions first; removing rust stack packages can pull **`ubuntu-minimal`** / **`ubuntu-server-minimal`** if nothing else keeps them installed. Always `./deoxidize --dry-run` first. The run **aborts unless the system is fully upgraded** (`--allow-outdated` to waive), **prints the full plan preview** (exact pin files and commands), and **asks for confirmation** before touching anything (`-y`/`--yes` to skip). Pick targets with `-i`, or undo with `-r`.

## Selective deoxidizing

Run only part of the system — every definition is still loaded and validated, but only the selected ones execute:

```bash
./deoxidize --list                    # rich listing: blocks, swaps, verify, sources
sudo ./deoxidize --only coreutils     # just the coreutils swap
sudo ./deoxidize --only coreutils,sudo   # comma-separated
sudo ./deoxidize --skip sudo          # everything except sudo
```

`--only`/`--skip` are comma-separated and repeatable; unknown names abort with the available list (`--only` wins if both are given). `-i`/`--interactive` (TTY only) shows a numbered picker instead; it cannot be combined with `--only`/`--skip`. `-r`/`--rollback` runs the inverse runbook for the selected set.

## Python engine

**`deoxidize`** (shim → the `src/deoxidize` package) is stdlib-only Python 3.11+ — no pip install, no runtime dependencies. All package policy lives in **`deoxidizers/*.toml`**; the engine has no package names in it. A deoxidizer declares what to **block** (APT pin), what to **swap in** (single-transaction install+remove), and how to **verify** (binary ownership + regex output tests).

```
src/deoxidize/
├── model.py      # dataclasses: Block, Swap, VerifyTest, Deoxidizer
├── config.py     # TOML loading/validation, --only/--skip selection
├── pins.py       # APT preferences rendering + writing
├── system.py     # apt/dpkg subprocess plumbing, dry-run support
├── runbook.py    # the staged step 1-8 execution plan
└── cli.py        # argument parsing, entry point
deoxidizers/      # declarative deoxidizer definitions (TOML)
legacy/           # frozen deoxidize.sh + notes, kept for reference
```

```bash
./deoxidize --list              # show loaded deoxidizers, no root needed
sudo ./deoxidize --dry-run      # print the full plan, change nothing
sudo ./deoxidize                # apply (same staged-pin order as deoxidize.sh)
sudo ./deoxidize --autoremove   # also run apt-get autoremove at the end
sudo ./deoxidize --no-allow-remove-essential   # abort instead of removing Essential pkgs
```

| Flag | Env fallback | Meaning |
|------|--------------|---------|
| `-n` / `--dry-run` | `DRY_RUN=1` | Print planned pins/commands; write nothing, run nothing. Assumes worst case (blocked packages installed) so the plan is complete. |
| `-a` / `--autoremove` | `AUTOREMOVE=1` | Run `apt-get autoremove` (step 5). Default off — old kernel headers/modules lines are safe but alarming. |
| `--allow-remove-essential` / `--no-…` | `ALLOW_REMOVE_ESSENTIAL=0` | Permit removing Essential packages (uutils is Essential=yes). |
| `-y` / `--yes` | — | Skip the pre-run confirmation prompt (required when stdin is not a TTY). |
| `--allow-outdated` | — | Skip the up-to-date pre-flight check (offline / air-gapped hosts). |
| `-i` / `--interactive` | — | Pick deoxidizers from a numbered TTY menu (conflicts with `--only`/`--skip`). |
| `-r` / `--rollback` | — | Undo the selected deoxidizers: remove pins and swap the blocked stack back in. |
| `--deoxidizers-dir` | — | Alternate TOML directory (default: `./deoxidizers` next to the script). |
| `--pref-dir` | — | Alternate APT preferences directory (default: `/etc/apt/preferences.d`). Useful for testing. |

Pins are written per deoxidizer as `/etc/apt/preferences.d/99-deoxidize-<name>.pref`; undo with `sudo rm -f /etc/apt/preferences.d/99-deoxidize-*.pref && sudo apt-get update`. If the legacy `99-block-sudo-rs-rust-coreutils.pref` is still present, the engine warns — remove it to keep undo simple.

**Deoxidizer format** — one TOML per target; current definitions:

- `deoxidizers/coreutils.toml` — GNU `coreutils` over `rust-coreutils`/`coreutils-from-uutils`
- `deoxidizers/sudo.toml` — GNU `sudo` over `sudo-rs`

```toml
[meta]
name = "coreutils"

[block]                       # pinned BEFORE surgery
packages = ["rust-coreutils"]
pin_phase = "early"

[swap]
install = ["coreutils-from-gnu"]
remove = ["coreutils-from-uutils"]
essential = true              # needs --allow-remove-essential
fallback = ["coreutils"]

[block.deferred]              # pinned only AFTER the swap lands
packages = ["coreutils-from-uutils"]
pin_phase = "post_swap"

[verify]
binaries = ["ls"]

# Output test: command output must match the regex (MULTILINE|DOTALL;
# triple-quoted strings allow multiline patterns). Any FAIL -> exit 1.
[[verify.tests]]
command = "ls --version"
expected = "GNU coreutils"
```

**`[[alternatives]]`** — optional; re-points `update-alternatives` master links after the swap so binaries routed through alternatives (e.g. `/usr/bin/sudo` on Ubuntu 25.10+) actually select the GNU path. `apply` is the registered path to `--set` when applying; `rollback` is the path to `--set` when rolling back (omit to leave the group alone). Groups or paths missing on a given system are skipped with a note, not an error:

```toml
[[alternatives]]
name = "sudo"                      # master link (e.g. /usr/bin/sudo)
apply = "/usr/bin/sudo.ws"         # GNU sudo's registered path
rollback = "/usr/lib/cargo/bin/sudo"  # sudo-rs's registered path
```

**`deoxidize.sh` lives in `legacy/`** — kept frozen as the reference implementation until the Python engine has survived one real upgrade cycle.

## Development

Tooling runs through **uv** (no global installs): `./lint_n_fmt.sh` syncs the dev group and runs **ruff** (check + format) and **pyright**. Config is in `pyproject.toml` (`[tool.ruff]`, `[tool.pyright]`); the runtime stays stdlib-only, dev dependencies live in `[dependency-groups]`.

---

## What the script does (in order)

| Step | Action |
|------|--------|
| **0** | **Pre-flight gates**: refresh package lists, then simulate `apt-get full-upgrade` and **abort unless the system is fully upgraded** (`--allow-outdated` waives this; dry-run warns instead). Then print the **full plan preview** — every pin file body and every APT command, exactly as they will be written/run — and ask **`Proceed? [y/N]`**. Anything but `y`/`yes` aborts with no changes (`--yes` skips the prompt; non-interactive stdin aborts unless `--yes`). |
| **1** | Writes **early** APT preferences: **`Pin-Priority: -1`** for **`sudo-rs`** and **`rust-coreutils`** only. **`coreutils-from-uutils` is not pinned yet** so APT can replace it cleanly. |
| **2** | **`apt-get update`** |
| **3** | Ensures **`sudo`** (GNU), then swaps to **`coreutils-from-gnu`**. If **`coreutils-from-uutils`** is installed, uses **`apt install coreutils-from-gnu coreutils-from-uutils-`** in one transaction (trailing **`-`** = remove that package) plus **`--allow-remove-essential`** because uutils is **Essential** on Ubuntu. Falls back to the legacy **`coreutils`** metapackage if the swap fails. Uses **`apt`** when available, else **`apt-get`**. |
| **3b** | **`update-alternatives --set`** for each configured **`[[alternatives]]`** group (e.g. points **`sudo`** at **`/usr/bin/sudo.ws`**). Skipped with a note when the group or path does not exist on this system. |
| **4** | Removes any still-installed **`sudo-rs`**, **`rust-coreutils`**, **`coreutils-from-uutils`** with **`apt-get --allow-remove-essential`** (metapackage transitions). Often empty after a successful step 3. |
| **4b** | Writes **full** preferences (adds **`coreutils-from-uutils`** pin) and **`apt-get update`** again. |
| **5** | **`apt-get autoremove`** only if **`AUTOREMOVE=1`** (default is **skip** — see below). |
| **6** | **`apt-get full-upgrade`** |
| **7** | **`apt-mark unhold`** on **`sudo`** and **`coreutils-from-gnu`** (cleanup). Blocked packages are **not** put on hold: with **Pin-Priority: -1** they have **no install candidate**, so **`apt-mark hold`** does not apply reliably. |
| **8** | Prints **`apt-cache policy`**, **`dpkg -l`**, and **`dpkg -S`** for **`sudo`** / **`ls`** (with **`readlink -f`** fallback for alternatives). |

Preferences file: **`/etc/apt/preferences.d/99-block-sudo-rs-rust-coreutils.pref`**

---

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| **`ALLOW_REMOVE_ESSENTIAL`** | **`1`** | Must stay **`1`** for step **3** (uutils swap) and step **4** (rust removals) when APT would remove **Essential** packages. Set **`0`** to abort instead of passing **`--allow-remove-essential`**. |
| **`AUTOREMOVE`** | **`0`** | If **`1`**, run **`apt-get autoremove`** in step 5. Default **skip**: autoremove often proposes old **`linux-headers-*` / `linux-modules-*`** trees for a **kernel you no longer boot** — usually safe but alarming and unrelated to this script. |
| **`DRY_RUN`** | **`0`** | If **`1`**, APT invocations print what would run and skip changes (see script). |

Examples:

```bash
sudo ./deoxidize.sh
AUTOREMOVE=1 sudo ./deoxidize.sh
ALLOW_REMOVE_ESSENTIAL=0 sudo ./deoxidize.sh   # aborts when Essential removal would be required
```

---

## Why staged pins and a same-transaction swap?

- Pinning **`coreutils-from-uutils`** to **`-1`** *before* swapping can confuse the solver into impossible **Conflicts** between GNU and uutils providers. This script defers that pin until **after** GNU is in place (**step 4b**).
- **`apt-get install coreutils-from-gnu`** alone can fail with **“two conflicting assignments”** while uutils remains selected. Installing **`coreutils-from-gnu`** and **`coreutils-from-uutils-`** together fixes that.
- Removing **Essential** **`coreutils-from-uutils`** requires **`--allow-remove-essential`** with noninteractive **`-y`** — same class of issue as step **4**.

---

## `ubuntu-minimal` and friends

**`ubuntu-minimal`** is a **metapackage**: almost no files; it **Depends** on a curated minimal set so upgrades can pull new “minimal Ubuntu” pieces. **`ubuntu-server-minimal`** is similar for server images.

If your only dependency on those metas was the rust stack, step **4** may **remove** them. Your actual utilities (**`coreutils-from-gnu`**, **`sudo`**, etc.) stay. You can **`sudo apt install --no-install-recommends ubuntu-minimal`** later if you want the metapackage back for tracking—always **`apt install -s …`** first.

---

## Project stance: GNU as the default worth defending

**GNU coreutils** (and **GNU `sudo`**) have been the **de facto baseline** on Linux for **decades**: scripts, CI, runbooks, vendor appliances, training material, and muscle memory all assume their behavior. That is not “GNU never bugs”—it is “**the burden of proof** for swapping the foundation belongs on whoever wants the churn.” If your machines are **not** failing on GNU today, **replacing the whole surface** is often **a solution in search of a problem**—**if it ain’t broke, don’t fix it**.

### Who pays the price on Ubuntu Server

**Server operators and sysadmins** are **hurt more than helped** when a distro quietly moves the goalposts on **`PATH`**: surprise diffs in pipelines, backup tooling, provisioning snippets, vendor scripts, and “works on my last LTS” automation. The upside for a typical **headless fleet**—fewer classic memory-safety bugs in coreutils—does not automatically outweigh **weeks of subtle breakage**, **re-validation**, and **re-training** for teams whose job is **uptime and predictability**, not **language fashion**.

### Trend-chasing vs. craft

**Greybeards** (and everyone who learned their trade on the same stack) are not ornaments. They represent **decades of hard-won knowledge** about how to **ship**, **debug**, and **recover** systems. **Sidelining a proven stack** to chase **trendiness**, **narrative**, or **pressure from a loud minority**—people with **strong opinions** but **no pager** on **your** outage—is how you **discard well-understood ways of building software** for **optics**. We call that out plainly: **respect the baseline** or **fork your own distro**; do not **pretend** a **forced rewrite** is a neutral “upgrade.”

### Why **opt-out** defaults are a bad fit here

Shipping the rust stack as **“you can turn it off”** still makes **everyone else** eat **discovery cost**, **compat risk**, and **support load**. For infrastructure that is the wrong default:

- **Security:** new surface, new bug classes, unfamiliar failure modes—not “Rust = safe,” **logic and integration** still bite.
- **Stability:** fewer surprises beats cleverer binaries on servers you touch once a year.
- **Compatibility / interoperability:** GNU behavior is what **the world already standardized on**; anything else is **tax** when you mix releases, vendors, and ages of images.

### **coreutils-from-gnu** and practical “superiority”

We do not claim GNU wins a beauty contest on **every** axis. **On the axes server people actually run on—history, ubiquity, and “what the rest of the ecosystem already assumes”—GNU coreutils is the stronger default:** more shared reality across machines, fewer “why did `sort` change” tickets, less **guesswork**. For us that is **practical superiority**; **history and ubiquity are not buzzwords**, they are **risk reducers**.

This script is also a response to **toxic framing**: **“my way or the highway”** packaging, **holier-than-thou** moralizing, and rhetoric that **denies operators a legitimate choice**. **Who runs the machine** decides **`PATH`**—not bystanders. We prefer **evidence and operator consent** over **pressure and fashion**, and we **take the system back** to **GNU on PATH** when **we** choose to run and support that baseline.

---

## Why maintainers of this repo avoid the Rust stack

This section mixes **stated project goals** with **cited, checkable facts**. The upstream projects are active and improving; the point here is that **parity and production readiness are non-trivial**, and some administrators prefer GNU until they are convinced otherwise.

### Rust / uutils coreutils (`rust-coreutils`, `coreutils-from-uutils`)

- **GNU test suite parity is not 100%.** The uutils project publishes continuous **GNU coreutils test coverage**; by definition anything less than full pass leaves behavioral gaps versus decades of GNU/scripts expectations. See the official dashboard: [GNU test coverage — uutils](https://uutils.github.io/coreutils/docs/test_coverage.html).
- **Release notes quantify failures and skips** against an updating GNU reference (e.g. pass/skip/fail counts when the reference moved to GNU 9.10): [uutils/coreutils 0.7.0 release notes](https://github.com/uutils/coreutils/releases/tag/0.7.0) (table under “GNU Test Suite Compatibility”).
- **Upstream treats GNU mismatches as bugs** — which acknowledges that differences still exist in the wild: [uutils/coreutils README](https://github.com/uutils/coreutils) (“Differences with GNU are treated as bugs”).
- **Reported performance regressions** (some later improved; the pattern is “not always a free win”) appear in public issues, e.g. large-file `base64` / `cksum` benchmarks: [#8574](https://github.com/uutils/coreutils/issues/8574), [#8573](https://github.com/uutils/coreutils/issues/8573); `ls -R /proc`: [#10662](https://github.com/uutils/coreutils/issues/10662); long-standing `factor`: [#1456](https://github.com/uutils/coreutils/issues/1456).
- **Ubuntu packaging context** (what `rust-coreutils` vs `coreutils-from-uutils` means on PATH, conflicts, switching): [Ask Ubuntu — difference between the two packages](https://askubuntu.com/questions/1564348/what-is-the-difference-between-coreutils-from-uutils-and-rust-coreutils).

### 94.74% parity means 5% divergence — and 5% is unacceptable here (as of Ubuntu 26.04 LTS)

uutils upstream reports **94.74% GNU test-suite parity** for 0.8.0 (630 of 665 tests passing against GNU 9.10) — the release Ubuntu 26.04 LTS ships as its **default userland** ([ComputingForGeeks guide](https://computingforgeeks.com/ubuntu-2604-rust-coreutils-guide/)). Read honestly, that is not "95% of GNU." It is a standing **1-in-20 chance that any given behavior of any given command differs** from what decades of scripts, vendor runbooks, documentation, and muscle memory assume. And the documented divergences are exactly the kind that surface in production rather than demos: `uname -p` returning `unknown` instead of the processor type, `stat` relabeling `Size:` to `size:` so case-sensitive parsers silently match nothing, `env -S` rejecting C-style escape sequences some shebangs rely on, `sort` collation differences in non-POSIX locales, a different `dd status=progress` update cadence. Canonical itself judged uutils `cp`, `mv`, and `rm` — with **eight unresolved TOCTOU races** between them — too data-destructive for an LTS and carved them back out to GNU.

That carve-out deserves its own verdict, because it is a quiet confession. **Critical TOCTOU vulnerabilities were serious enough that `cp`, `mv`, and `rm` — the three most data-destructive binaries in the suite — could not ship in an LTS.** Replacing a decades-old stack with software that broken would be unacceptable even if the addition had been surgical and limited to those tools; the fact that the **parent metapackage was slated for inclusion with the entire uutils suite, fully carrying those issues**, while **GNU has none of that history** — no carve-outs, no data-loss holds, no "reassess at the next release" — is completely antithetical to system stability and reliability. An LTS vendor who has to surgically subtract the dangerous parts of a replacement before shipping it has already made the argument for keeping the original.

The payoff for absorbing that risk is small. The guide's own benchmarks are a wash: uutils wins `cat` (2.3x, via a `splice()` fast path) and `sort` (~12%); GNU wins `sha256sum` (2.3x, via hand-tuned assembly). What remains as justification is **better DX and the elimination of memory-corruption vulnerability classes** — real, but modest gains against **uprooting decades of stability and familiarity**. Even the memory-safety argument is weaker than advertised: Canonical's Zellic audit of uutils found **113 issues, 41 of them CVE-worthy** — memory safety does not buy logic-bug immunity, and the supply-chain surface grows to ~50 crates per binary. When the system is not failing today, trading a known, boring, universal baseline for that package deal fails the burden-of-proof test above.

### `sudo-rs`

- **Security fixes have shipped for logic bugs**, not “only C memory issues”: Ubuntu documents issues such as mishandled passwords on timeout / `pwfeedback` interaction and timestamp handling: [USN-7867-1: sudo-rs vulnerabilities](https://ubuntu.com/security/notices/USN-7867-1).
- **Additional CVE-class issues** are tracked in the usual databases (e.g. sudoers enumeration): [CVE-2025-46718 (Rapid7 summary)](https://www.rapid7.com/db/vulnerabilities/ubuntu-cve-2025-46718/) — always cross-check with [Ubuntu CVE pages](https://ubuntu.com/security/cves) and your release’s USN list.

**Project opinion:** Rust reduces some classic memory-safety bug classes, but **`sudo-rs` is still a young surface area** with different defaults and bug history than the C `sudo` ecosystem many tools and humans assume. Preferring GNU `sudo` is a legitimate stability/consistency choice.

---

## Rollback (`-r`)

The engine can undo itself for the selected deoxidizers (`--only`/`--skip`/`-i` all work here too):

```bash
sudo ./deoxidize -r                 # roll back everything
sudo ./deoxidize -r --only sudo    # roll back just the sudo swap
sudo ./deoxidize -r -n             # preview the rollback, change nothing
```

Rollback stages (R1-R4):

| Step | Action |
|------|--------|
| **R1** | Removes `/etc/apt/preferences.d/99-deoxidize-<name>.pref` for each selected deoxidizer (plus the legacy `99-block-sudo-rs-rust-coreutils.pref` if present). |
| **R2** | `apt-get update` — the blocked stack becomes installable again. |
| **R3** | One same-transaction inverse swap per deoxidizer: reinstall the previously blocked packages and remove the GNU replacement (e.g. `apt install rust-coreutils coreutils-from-uutils coreutils-from-gnu-`). Skipped when the replacement was never installed. Configured **`[[alternatives]]`** groups are re-pointed at their **`rollback`** path (e.g. `sudo` → `/usr/lib/cargo/bin/sudo`). |
| **R4** | `apt-get update` again, then `apt-cache policy` for the restored packages. |

Notes: rollback **skips the up-to-date gate** on purpose — undo must work on a broken or offline system — and `ALLOW_REMOVE_ESSENTIAL=0` does not block it (the GNU side is not Essential; that flag guards the forward swap's uutils removal). Like apply, rollback prints the exact files to delete and exact commands to run, then asks **`Proceed? [y/N]`**.

### Manual undo (fallback)

```bash
sudo rm -f /etc/apt/preferences.d/99-block-sudo-rs-rust-coreutils.pref
sudo apt-get update
```

Reinstall metapackages if you removed them and want them back:

```bash
sudo apt install --no-install-recommends ubuntu-minimal
```

Always review what APT plans to pull in (`apt-cache policy`, `apt install -s …`) **before** confirming.

---

## References (numbered)

1. uutils — GNU test coverage dashboard: https://uutils.github.io/coreutils/docs/test_coverage.html  
2. uutils/coreutils — README (GNU differences treated as bugs): https://github.com/uutils/coreutils  
3. uutils/coreutils — 0.7.0 release (GNU test suite table): https://github.com/uutils/coreutils/releases/tag/0.7.0  
4. Ubuntu — USN-7867-1 (`sudo-rs` fixes): https://ubuntu.com/security/notices/USN-7867-1  
5. Ask Ubuntu — `coreutils-from-uutils` vs `rust-coreutils`: https://askubuntu.com/questions/1564348/what-is-the-difference-between-coreutils-from-uutils-and-rust-coreutils  
6. Ask Ubuntu — rationale discussion for Ubuntu’s direction: https://askubuntu.com/questions/1564801/why-did-ubuntu-switch-from-gnu-coreutils-to-uutils  
7. uutils/coreutils — performance / parity issues (examples): [#8574](https://github.com/uutils/coreutils/issues/8574), [#8573](https://github.com/uutils/coreutils/issues/8573), [#10662](https://github.com/uutils/coreutils/issues/10662), [#1456](https://github.com/uutils/coreutils/issues/1456)  
8. Heise (English) — coverage article citing GNU test suite pass rate for a release line: https://www.heise.de/en/news/Rust-Coreutils-0-6-reaches-96-percent-GNU-compatibility-11163476.html  
9. ComputingForGeeks — Ubuntu 26.04 Rust Coreutils guide (94.74% parity for 0.8.0, documented divergences, benchmarks, Zellic audit findings): https://computingforgeeks.com/ubuntu-2604-rust-coreutils-guide/

---

*This README was drafted with **Composer 2** in **Cursor**.*
