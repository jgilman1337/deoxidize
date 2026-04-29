# deoxidize

Bash helper for **Debian/Ubuntu-style** systems that **prefer GNU `coreutils` and GNU `sudo`** over the Rust-based stack Ubuntu has been moving toward (`coreutils-from-uutils`, `rust-coreutils`, `sudo-rs`).

**Quick start:** `chmod +x deoxidize.sh` then `sudo ./deoxidize.sh` from a root-capable session (local console or a shell where `sudo` still works). Read the script first; removing rust stack packages can pull **`ubuntu-minimal`** / **`ubuntu-server-minimal`** if nothing else keeps them installed.

---

## What the script does (in order)

| Step | Action |
|------|--------|
| **1** | Writes **early** APT preferences: **`Pin-Priority: -1`** for **`sudo-rs`** and **`rust-coreutils`** only. **`coreutils-from-uutils` is not pinned yet** so APT can replace it cleanly. |
| **2** | **`apt-get update`** |
| **3** | Ensures **`sudo`** (GNU), then swaps to **`coreutils-from-gnu`**. If **`coreutils-from-uutils`** is installed, uses **`apt install coreutils-from-gnu coreutils-from-uutils-`** in one transaction (trailing **`-`** = remove that package) plus **`--allow-remove-essential`** because uutils is **Essential** on Ubuntu. Falls back to the legacy **`coreutils`** metapackage if the swap fails. Uses **`apt`** when available, else **`apt-get`**. |
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

### `sudo-rs`

- **Security fixes have shipped for logic bugs**, not “only C memory issues”: Ubuntu documents issues such as mishandled passwords on timeout / `pwfeedback` interaction and timestamp handling: [USN-7867-1: sudo-rs vulnerabilities](https://ubuntu.com/security/notices/USN-7867-1).
- **Additional CVE-class issues** are tracked in the usual databases (e.g. sudoers enumeration): [CVE-2025-46718 (Rapid7 summary)](https://www.rapid7.com/db/vulnerabilities/ubuntu-cve-2025-46718/) — always cross-check with [Ubuntu CVE pages](https://ubuntu.com/security/cves) and your release’s USN list.

**Project opinion:** Rust reduces some classic memory-safety bug classes, but **`sudo-rs` is still a young surface area** with different defaults and bug history than the C `sudo` ecosystem many tools and humans assume. Preferring GNU `sudo` is a legitimate stability/consistency choice.

---

## Undo

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

---

*This README was drafted with **Composer 2** in **Cursor**.*
