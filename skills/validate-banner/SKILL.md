---
name: validate-banner
description: Validate an HTML5 banner (zip archive or single HTML file) against ad-network requirements — a named network, all networks, or auto-detected. Use when the user asks to "validate this banner/zip/creative", "check this against <network> specs", "which network does this banner fit", "will Google Ads / Amazon DSP / Yandex accept this", or drops a banner zip and asks if it's OK. Reports errors/warnings with fix suggestions, and can auto-fix the two most common rejections with --fix — strip non-whitelisted external URLs (Amazon DSP) and compress images to fit the size limit, writing a fixed copy.
---

# validate-banner

Validates a banner bundle with the same engine as the banner-validator web app
(same ruleset, same validators). Ships two interchangeable runners; **prefer
Python** — it needs no install and runs where Node isn't available.

## How to run

**Python (preferred):**

```bash
python3 <skill-dir>/validate.py <banner.zip | banner.html> [options]
```

Requires Python ≥ 3.9. Stdlib-only for validation and the URL-strip `--fix`; the
`--fix` image-compression step additionally needs Pillow (`pip install Pillow`) —
without it, `--fix` still strips URLs and says so.

**Node (fallback, only if Python is unavailable):**

```bash
node <skill-dir>/validate.mjs <banner.zip | banner.html> [options]
```

Requires Node ≥ 20. Both runners take identical flags and produce identical
verdicts; the table below uses `validate.py`.

`<skill-dir>` is this skill's own directory. No install step for either runner.

| User intent | Invocation |
| --- | --- |
| "validate for <network>" | `validate.py banner.zip --network <id>` (find ids via `--list-networks`) |
| "which networks fit?" / "check all" | `validate.py banner.zip --all` |
| no network mentioned | `validate.py banner.zip` — auto-detects from vendor script markers, falls back to the all-network matrix |
| "fix it" / "make it pass <network>" | `validate.py banner.zip --network <id> --fix` — writes a fixed copy (see below) |

Add `--json` when you want to post-process results programmatically.

Exit codes: `0` pass/warnings, `1` errors (matrix mode: errors on **every**
network — fits nowhere), `2` usage or I/O error.

## Auto-fix (`--fix`)

`--fix` applies the same fixes as the web tool for the chosen network, then writes a
fixed copy (default `<input>_fixed.<zip|html>`, or `-o <path>`) and reports the
before→after verdict. It needs a **single** network (`--network <id>`, or one the
banner auto-detects). It applies, in order:

1. **Strip external URLs** — for whitelist-mode networks (Amazon DSP / DSP 2.0),
   blanks every non-allowlisted `http(s)` reference in place (keeps the Amazon SDK,
   `amazon-adsystem.com`, `amazon.com`, `greensock.com`, and inert `w3.org` xmlns).
   Pure stdlib — works on the Python runner with no extra install.
2. **Compress to fit** — if still over the size limit, re-encodes raster images
   (JPEG/PNG; WebP left as-is, animated GIF skipped) at the highest quality that fits.
   On the Python runner this needs Pillow; if it's not installed, `--fix` does step 1
   and reports that compression was skipped (install Pillow or use the Node runner).

Compression is **lossy** — tell the user to eyeball the fixed creative. The fix is
non-destructive: it writes a new file and never touches the input.

## Interpreting the report

- Severities: **error** = the network will reject the banner; **warning** =
  likely fine but worth a look; **info** = purely informational and never
  affects the verdict (e.g. allowed external references).
- Size checks gate on the **zipped** weight for zip input and raw size for a
  single HTML file; an uncompressed-over-limit case appears as a warning.
- The auto-detect evidence line shows which vendor `<script src>` markers
  matched. Networks without markers (Google, Yandex, generic) are never
  auto-detectable — that's expected, not a failure.
- The external-URL check scans raw text (like Amazon's own validator), so URLs
  inside JSON or script metadata are flagged intentionally.

## Giving fix guidance

- Relay each failed check's `fix:` line (it comes from the network rule's own
  `suggestion`).
- For **external-URL** or **size** errors, offer `--fix` — it strips URLs and/or
  compresses images and writes a fixed copy in one step. Confirm the input first
  (external URLs that are genuine runtime dependencies shouldn't be stripped; lossy
  compression can soften the creative).
- For other failures (missing `clickTag`, wrong dimensions, missing special files),
  `--fix` does nothing — those need human edits; relay the `fix:` suggestion.

## Maintenance

Two artifacts live here. **Both are kept in sync from the banner-validator repo —
do not hand-edit the generated ones.**

- `rules.json` — the **ruleset, generated** from `src/rules/networks.ts` (the single
  source of truth) by `npm run build:rules`. Both runners read it. The Python package
  loads it; the Node bundle inlines it. `tests/rulesJson.test.ts` fails when it's stale.
- `validate.mjs` — the **Node bundle, generated** (esbuild of `src/cli/validate.ts` +
  `src/rules` + `src/validators`). `tests/skillArtifact.test.ts` fails when it's stale.
- `validate.py` + `bannerlib/` — the **Python runner, hand-maintained** (a faithful
  port of the same engine). Covered by `tests-py/` (including a Node↔Python parity
  test). Edit these directly when behaviour changes, and keep them in step with the
  TS source.

After changing rules/validators/input adapters, run `npm run build:skill` (refreshes
both generated artifacts), update `validate.py`/`bannerlib/` to match, and re-run
`npm run test` + `npm run test:py`.
