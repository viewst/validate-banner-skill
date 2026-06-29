"""Thin CLI over the validation pipeline — Python counterpart of src/cli/validate.ts.
Same flags, same modes (single / auto-detect / matrix), same text + --json output,
same exit codes (0 pass/warn, 1 errors, 2 usage/IO)."""
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .bundle import BannerBundle, all_text
from .fixes import (
    PIL_AVAILABLE,
    apply_external_url_fix,
    compress_to_fit,
    has_compressible_images,
    pack_zip,
)
from .format import format_bytes
from .inputs import bundle_from_file_bytes, bundle_from_zip_bytes, detect_network
from .rules import NetworkRule, get_network, visible_networks
from .validators import Check, ValidationReport, validate

USAGE = """Usage: python3 validate.py <banner.zip | banner.html> [options]

Options:
  --network <id>    Validate against one network (see --list-networks)
  --all             Validate against every visible network (compatibility matrix)
  --fix             Auto-fix fixable errors for the chosen network and write a
                    fixed copy: strip non-whitelisted external URLs (Amazon DSP)
                    and/or compress images to fit the size limit. Needs a single
                    network (--network, or an auto-detected one).
  -o, --output <p>  Output path for --fix (default: <input>_fixed.<zip|html>)
  --json            Machine-readable output
  --list-networks   Print known network ids and exit
  -h, --help        Show this help

Default mode (no --network/--all): auto-detect the network from vendor script
markers; falls back to the all-networks matrix when undetectable.

Exit codes: 0 = pass/warnings, 1 = errors, 2 = usage or I/O error.
(Matrix mode exits 1 only when every network reports errors.)"""


@dataclass
class Args:
    path: Optional[str] = None
    network: Optional[str] = None
    all: bool = False
    json: bool = False
    list: bool = False
    help: bool = False
    fix: bool = False
    output: Optional[str] = None


def parse_args(argv: List[str]) -> Args:
    args = Args()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--all":
            args.all = True
        elif a == "--json":
            args.json = True
        elif a == "--list-networks":
            args.list = True
        elif a in ("--help", "-h"):
            args.help = True
        elif a == "--fix":
            args.fix = True
        elif a == "--network":
            i += 1
            v = argv[i] if i < len(argv) else None
            if not v or v.startswith("--"):
                raise ValueError("--network requires a network id.")
            args.network = v
        elif a in ("--output", "-o"):
            i += 1
            v = argv[i] if i < len(argv) else None
            if not v or v.startswith("--"):
                raise ValueError("--output requires a path.")
            args.output = v
        elif a.startswith("--"):
            raise ValueError(f'Unknown option "{a}".')
        elif args.path:
            raise ValueError("Only one input file is supported.")
        else:
            args.path = a
        i += 1
    return args


def load_bundle(path: str) -> BannerBundle:
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".zip", ".html", ".htm"):
        raise ValueError(f'Unsupported file type "{ext or path}" — expected .zip, .html or .htm.')
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        raise ValueError(f"Cannot read file: {path}")
    if ext == ".zip":
        return bundle_from_zip_bytes(raw, len(raw))
    return bundle_from_file_bytes(os.path.basename(path), raw)


def matched_markers(bundle: BannerBundle, rule: NetworkRule) -> List[str]:
    haystack = "\n".join(text for _, text in all_text(bundle)).lower()
    return [s for s in rule.required_scripts if s and s.lower() in haystack]


_ICONS = {"error": "✖", "warning": "⚠", "info": "ℹ", "pass": "✔"}
_VERDICT_ORDER = {"pass": 0, "info": 0, "warning": 1, "error": 2}


def _header_lines(path: str, bundle: BannerBundle) -> List[str]:
    if bundle.zipped_bytes is not None and bundle.source == "zip":
        size = f"{format_bytes(bundle.zipped_bytes)} zipped, {format_bytes(bundle.total_bytes)} raw"
    else:
        size = format_bytes(bundle.total_bytes)
    return [
        f"Banner: {path} ({bundle.source}, {size})",
        *[f"Note: {w}" for w in bundle.warnings],
    ]


def _check_lines(report: ValidationReport) -> List[str]:
    lines: List[str] = []
    for c in report.checks:
        lines.append(f"  {_ICONS[c.severity]} [{c.id}] {c.title} — {c.detail}")
        if c.evidence:
            lines.append(f"      evidence: {c.evidence}")
        if c.suggestion and c.severity != "pass":
            lines.append(f"      fix: {c.suggestion}")
    return lines


def _single_network_text(path: str, bundle: BannerBundle, report: ValidationReport, detection_note: str) -> str:
    r = report
    return "\n".join([
        *_header_lines(path, bundle),
        "",
        f"Network: {r.network.label} ({r.network.id}){detection_note}",
        "",
        *_check_lines(r),
        "",
        f"Verdict: {r.verdict.upper()} — {r.errors} error(s), {r.warnings} warning(s), "
        f"{r.passed} passed, {r.infos} info",
    ])


def _matrix_text(path: str, bundle: BannerBundle, reports: List[ValidationReport], auto: bool) -> str:
    sorted_reports = sorted(reports, key=lambda r: (_VERDICT_ORDER[r.verdict], r.network.label))
    rows = []
    for r in sorted_reports:
        counts = "" if r.verdict == "pass" else f" — {r.errors} error(s), {r.warnings} warning(s)"
        rows.append(
            f"  {_ICONS[r.verdict]} {r.verdict.upper().ljust(7)} {r.network.label} ({r.network.id}){counts}"
        )
    fits = [r.network.id for r in sorted_reports if r.verdict != "error"]
    return "\n".join([
        *_header_lines(path, bundle),
        "",
        (
            f"No network auto-detected — validating against all {len(reports)} visible networks."
            if auto
            else f"Validating against all {len(reports)} visible networks."
        ),
        "",
        *rows,
        "",
        (f"Fits (no errors): {', '.join(fits)}" if fits else "Fits nowhere: every network reports errors."),
    ])


def _check_to_dict(c: Check) -> dict:
    d = {"id": c.id, "severity": c.severity, "title": c.title, "detail": c.detail}
    if c.suggestion is not None:
        d["suggestion"] = c.suggestion
    if c.evidence is not None:
        d["evidence"] = c.evidence
    return d


def _report_json(r: ValidationReport) -> dict:
    return {
        "networkId": r.network.id,
        "label": r.network.label,
        "verdict": r.verdict,
        "errors": r.errors,
        "warnings": r.warnings,
        "infos": r.infos,
        "passed": r.passed,
        "checks": [_check_to_dict(c) for c in r.checks],
    }


def _default_fixed_path(input_path: str, source: str) -> str:
    ext = ".zip" if source == "zip" else ".html"
    base = re.sub(r"\.(zip|html?|htm)$", "", input_path, flags=re.I)
    return f"{base}_fixed{ext}"


def _write_fixed_bundle(bundle: BannerBundle, out_path: str) -> None:
    if bundle.source == "zip":
        with open(out_path, "wb") as fh:
            fh.write(pack_zip(bundle))
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(bundle.entry_html)


def run_fix(args: Args, bundle: BannerBundle, rule: Optional[NetworkRule], detected: Optional[str]) -> Tuple[int, str]:
    if rule is None:
        why = " (none auto-detected)" if detected is None else ""
        return (2, f"--fix needs a single network{why}. Pass --network <id> (see --list-networks).")

    before = validate(bundle, rule)
    fixed = bundle
    notes: List[str] = []

    if rule.external_urls.get("mode") == "whitelist":
        r = apply_external_url_fix(fixed, rule)
        if r.removed:
            fixed = r.bundle
            hosts = list(dict.fromkeys(
                re.sub(r"^https?://", "", u).split("/")[0] for u in r.removed
            ))
            notes.append(
                f"Stripped {len(r.removed)} non-whitelisted external URL(s) from "
                f"{len(hosts)} host(s): {', '.join(hosts[:5])}."
            )

    size_after = next((c for c in validate(fixed, rule).checks if c.id == "size"), None)
    if size_after and size_after.severity == "error":
        if has_compressible_images(fixed):
            if not PIL_AVAILABLE:
                notes.append(
                    "Still over the size limit. Image compression needs Pillow — install it "
                    "with `pip install Pillow` and re-run --fix (or use the Node runner)."
                )
            else:
                cr = compress_to_fit(fixed, rule)
                fixed = cr.bundle
                tail = "" if cr.fit else " (best effort — still over the limit)"
                notes.append(
                    f"Compressed images {format_bytes(cr.before_bytes)} -> {format_bytes(cr.after_bytes)} "
                    f"at quality {round(cr.quality * 100)}%{tail}."
                )
                if cr.skipped:
                    notes.append(f"Skipped {len(cr.skipped)} animated GIF(s).")
        else:
            notes.append("Over the size limit, but no compressible images were found.")

    if not notes:
        return (
            1 if before.verdict == "error" else 0,
            f"Nothing to auto-fix for {rule.label} — no fixable external-URL or size errors.",
        )

    out_path = args.output or _default_fixed_path(args.path, fixed.source)
    try:
        _write_fixed_bundle(fixed, out_path)
    except Exception as e:
        return (2, f'Fixes applied but writing "{out_path}" failed: {e}')

    after = validate(fixed, rule)
    lines = [
        f"Auto-fix for {rule.label} ({rule.id}):",
        *[f"  - {n}" for n in notes],
        "",
        f"Verdict: {before.verdict.upper()} -> {after.verdict.upper()} "
        f"({after.errors} error(s), {after.warnings} warning(s), {after.passed} passed).",
        f"Wrote: {out_path}",
    ]
    return (1 if after.verdict == "error" else 0, "\n".join(lines))


def run_cli(argv: List[str]) -> Tuple[int, str]:
    try:
        args = parse_args(argv)
    except ValueError as e:
        return (2, f"{e}\n\n{USAGE}")

    if args.help:
        return (0, USAGE)
    if args.list:
        rows = [f"{n.id.ljust(26)} {n.label}" for n in visible_networks()]
        return (0, "\n".join(rows))
    if not args.path:
        return (2, USAGE)
    if args.network and args.all:
        return (2, f"Use either --network or --all, not both.\n\n{USAGE}")

    rule: Optional[NetworkRule] = None
    if args.network:
        rule = get_network(args.network)
        if rule is None:
            ids = "\n".join(f"  {n.id}" for n in visible_networks())
            return (2, f'Unknown network "{args.network}". Known ids:\n{ids}')

    try:
        bundle = load_bundle(args.path)
    except ValueError as e:
        return (2, str(e))

    mode = "all" if args.all else ("network" if rule else "auto")
    detected: Optional[str] = None
    if mode == "auto":
        detected = detect_network(bundle)
        if detected:
            rule = get_network(detected)

    if args.fix:
        return run_fix(args, bundle, rule, detected)

    rules = [rule] if rule else visible_networks()
    reports = [validate(bundle, r) for r in rules]
    if rule:
        exit_code = 1 if reports[0].verdict == "error" else 0
    else:
        exit_code = 1 if all(r.verdict == "error" for r in reports) else 0

    if args.json:
        payload = {
            "input": args.path,
            "source": bundle.source,
            "mode": mode,
            "detected": detected,
            "bundleWarnings": bundle.warnings,
            "reports": [_report_json(r) for r in reports],
        }
        return (exit_code, json.dumps(payload, indent=2, ensure_ascii=False))

    if rule:
        note = ""
        if detected:
            markers = ", ".join(f'"{m}"' for m in matched_markers(bundle, rule))
            note = f" — auto-detected via {markers}"
        return (exit_code, _single_network_text(args.path, bundle, reports[0], note))
    return (exit_code, _matrix_text(args.path, bundle, reports, mode == "auto"))


def main(argv: List[str]) -> int:
    # Ensure the ✓/✗/⚠/ℹ icons render even when the locale isn't UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    exit_code, output = run_cli(argv)
    print(output, file=sys.stderr if exit_code == 2 else sys.stdout)
    return exit_code
