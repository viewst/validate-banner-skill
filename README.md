# validate-banner

Validate an HTML5 banner (a `.zip` archive or a single `.html` file) against
ad-network requirements — **Google Ads, Amazon DSP, Yandex, The Trade Desk, Xandr,
Sizmek, BidTheatre, and 25+ more** — and auto-fix the two most common rejections.

It's both a **Claude Code / agent skill** (drop a banner in chat and ask "will this
pass Amazon DSP?") and a plain **command-line tool**. The validation engine is the
same one behind Viewst's banner-validator web app: same ruleset, same checks.

```text
$ python3 validate.py creative.zip --network amazon_2dsp
Banner: creative.zip (zip, 198 KB zipped, 240 KB raw)

Network: Amazon DSP (2.0) (amazon_2dsp)

  ✔ [entry] Entry HTML found — Using "index.html" as the banner entry point.
  ✔ [size] Within size limit — Zipped weight is 198 KB, under the 200 KB limit…
  ✖ [external-urls] 1 forbidden external reference — … Found: fonts.googleapis.com.
      fix: Download and bundle these assets locally so no external URL remains.

Verdict: ERROR — 1 error(s), 0 warning(s), 8 passed, 0 info
```

---

## Why Python

The validator is **stdlib-only** — no `pip install`, no Node, no build step. Copy
the folder onto any machine with `python3` and run it. (A bundled Node runner,
`validate.mjs`, is included as a fallback for Node-only environments.)

| Capability | Needs |
| --- | --- |
| Validate (all networks, single, auto-detect) | Python ≥ 3.9 — **nothing else** |
| `--fix` strip external URLs (Amazon DSP) | Python ≥ 3.9 — **nothing else** |
| `--fix` compress images to fit the size limit | Python ≥ 3.9 **+ [Pillow](https://pypi.org/project/Pillow/)** |
| Node fallback runner (`validate.mjs`) | Node ≥ 20 |

If Pillow isn't installed, `--fix` still strips external URLs and tells you to
`pip install Pillow` (or use the Node runner) for the compression step.

---

## Install

### 1. `npx skills add` (easiest, for AI coding agents)

The [open `skills` CLI](https://github.com/vercel-labs/skills) installs this skill
into Claude Code, Cursor, Codex, Copilot, and others — auto-detecting which agents
you have:

```bash
npx skills add viewst/validate-banner-skill
```

This needs Node/`npx` to *install*, but the skill itself still runs on Python.
See **[docs/installing-with-npx-skills.html](docs/installing-with-npx-skills.html)**
for exactly how discovery, agent targeting, and install paths work.

### 2. Plain copy (no Node required)

Clone (or download the ZIP) and copy the skill folder where your agent looks for
skills — e.g. Claude Code's personal skills dir:

```bash
git clone https://github.com/viewst/validate-banner-skill.git
cp -R validate-banner-skill/skills/validate-banner ~/.claude/skills/
```

Or use it purely as a CLI from anywhere:

```bash
python3 validate-banner-skill/skills/validate-banner/validate.py mybanner.zip
```

### 3. Claude Code plugin

```text
/plugin marketplace add viewst/validate-banner-skill
/plugin install validate-banner@viewst
```

---

## Usage

```bash
python3 validate.py <banner.zip | banner.html> [options]
```

| You want to… | Command |
| --- | --- |
| Check against one network | `validate.py banner.zip --network amazon_2dsp` |
| See which networks it fits | `validate.py banner.zip --all` |
| Let it figure out the network | `validate.py banner.zip` (auto-detects, else checks all) |
| Auto-fix and write a fixed copy | `validate.py banner.zip --network amazon_2dsp --fix` |
| List network ids | `validate.py --list-networks` |
| Machine-readable output | add `--json` |

### Options

```
  --network <id>    Validate against one network (see --list-networks)
  --all             Validate against every visible network (compatibility matrix)
  --fix             Auto-fix fixable errors and write a fixed copy (strip external
                    URLs and/or compress images). Needs a single resolved network.
  -o, --output <p>  Output path for --fix (default: <input>_fixed.<zip|html>)
  --json            Machine-readable output
  --list-networks   Print known network ids and exit
  -h, --help        Show help
```

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Passed (or warnings only) |
| `1` | Errors — the network would reject it (matrix mode: errors on **every** network) |
| `2` | Usage or I/O error |

### Severities

- **error** — the network will reject the banner.
- **warning** — likely fine, but worth a look.
- **info** — purely informational (e.g. allowed external references); never changes the verdict.
- **pass** — satisfied.

---

## `--fix` (auto-repair)

`--fix` applies the same repairs the Viewst web tool offers, for one resolved
network, then writes a **new** file (`<input>_fixed.<zip|html>`, or `-o <path>`) —
it never touches the input. It applies, in order:

1. **Strip external URLs** — for whitelist-mode networks (Amazon DSP / DSP 2.0),
   blanks every non-allowlisted `http(s)` reference in place, keeping the Amazon SDK,
   `amazon-adsystem.com`, `amazon.com`, `greensock.com`, and inert `w3.org` xmlns.
2. **Compress to fit** — if still over the size limit, re-encodes raster images
   (JPEG/PNG; WebP left as-is, animated GIF skipped) at the highest quality that fits.
   Requires Pillow.

Compression is **lossy** — eyeball the fixed creative. Other failures (missing
`clickTag`, wrong dimensions, missing special files) need human edits; `--fix`
relays the suggestion but won't touch them.

---

## Supported networks

`--list-networks` prints the live list. Currently:

**Google** — Google Ads, Google Campaign Manager, Google Display & Video 360
**Amazon** — Amazon DSP (2.0)
**DSPs / ad-servers** — The Trade Desk, Xandr (AppNexus), Xandr Invest, Smart AdServer,
Sizmek, BidTheatre, Choozle, Delta Projects, Ströer, Quantcast, Beeswax, AdRoll,
Verizon, Yahoo Ad Tech
**Generic** — Default / Custom

Additional legacy/internal networks (Amazon DSP 1.0, Yandex Direct/Display, and several
Russian ad-servers) are validatable by passing their id to `--network` even though they
don't appear in `--list-networks`.

---

## How it decides

- **Size** gates on the **zipped** weight for ZIP uploads (the raw size for a single
  HTML file); an uncompressed-over-limit case is a separate warning.
- **Animation duration** is always surfaced with the selected network's policy.
  Unknown or dynamically generated timing stays informational; only a statically
  explicit overrun or infinite loop raises a warning.
- **Auto-detect** keys off vendor `<script src>` markers a network's export injects.
  Networks without a marker (Google, Yandex, generic) aren't auto-detectable — that's
  expected; the tool then validates against the full matrix.
- **External-URL** scanning reads **raw text** (Amazon's own validator is a grep, not a
  runtime check), so URLs inside JSON or script metadata are flagged intentionally.

- **Click behavior** checks catch `javascript:` links that open blank tabs, assess
  Viewst click zones against each network's policy, and warn about hard-coded
  landing URLs. These checks inspect source without executing click handlers.

---

## Provenance & maintenance

The ruleset (`skills/validate-banner/rules.json`) is **generated** from the
[banner-validator](https://github.com/viewst) project's single source of truth and
mirrors Viewst's live HTML5 banner export pipeline. The Python engine
(`skills/validate-banner/bannerlib/`) is a faithful port of the same validators, kept
honest by a Node↔Python parity test. This repository is the published distribution;
upstream changes are re-synced here.

## License

[Apache-2.0](LICENSE) © Viewst. See [NOTICE](NOTICE).
