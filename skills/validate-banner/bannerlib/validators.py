"""The pure validators + the registry that aggregates them into a report.
Ports src/validators/*.ts. Each validator is (bundle, rule) -> List[Check].
Severity convention: error = the network rejects it; warning = worth a look;
info = purely informational (never elevates the verdict); pass = satisfied."""
import json
import re
from dataclasses import dataclass
from typing import List, Optional

from .animation import analyze_animation
from .bundle import BannerBundle, all_text, base_name, ext_of, find_by_name
from .format import format_bytes
from .htmlprobe import (
    dimensions_from_css,
    dimensions_from_name,
    extract_urls,
    has_banner_click,
    has_meta_ad_size,
    host_of,
    parse_ad_size,
)
from .rules import NetworkRule


@dataclass
class Check:
    id: str
    severity: str  # "error" | "warning" | "info" | "pass"
    title: str
    detail: str
    suggestion: Optional[str] = None
    evidence: Optional[str] = None


@dataclass
class ValidationReport:
    network: NetworkRule
    checks: List[Check]
    errors: int
    warnings: int
    infos: int
    passed: int
    verdict: str


def _haystack(bundle: BannerBundle) -> str:
    return "\n".join(text for _, text in all_text(bundle))


# ---- structure --------------------------------------------------------------

def structure_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    checks: List[Check] = []

    if bundle.entry_path:
        checks.append(Check(
            id="entry", severity="pass", title="Entry HTML found",
            detail=f'Using "{bundle.entry_path}" as the banner entry point.',
        ))
    else:
        checks.append(Check(
            id="entry", severity="error", title="No entry HTML",
            detail="The banner must contain an index.html (or another .html) as its entry point.",
            suggestion="Add an index.html at the archive root.",
        ))

    for sf in rule.special_files:
        found = find_by_name(bundle, sf.name)
        checks.append(Check(
            id=f"special-file:{sf.name}",
            severity="pass" if found else ("error" if sf.required else "warning"),
            title=f"{sf.name} present" if found else f"Missing {sf.name}",
            detail=(
                f'Required file "{sf.name}" is present.'
                if found
                else f'{rule.label} expects a "{sf.name}" file. {sf.note or ""}'.strip()
            ),
            suggestion=None if found else sf.note,
        ))

    nested = [f for f in bundle.files if ext_of(f.path) == "zip"]
    if nested:
        names = ", ".join(base_name(f.path) for f in nested)
        checks.append(Check(
            id="nested-archive", severity="warning", title="Nested archive detected",
            detail=(
                f"The upload contains {len(nested)} inner .zip file(s): {names}. "
                "Networks expect a flat creative, not a zip-of-zips."
            ),
            suggestion="Unpack the inner archive and re-zip a single creative.",
        ))

    for i, w in enumerate(bundle.warnings):
        if w.startswith("No HTML entry"):
            continue  # already reported above
        checks.append(Check(
            id=f"bundle-warning:{i}", severity="warning", title="Packaging note", detail=w,
        ))

    return checks


# ---- size -------------------------------------------------------------------

def size_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    limit = rule.size_limit_bytes
    measured = bundle.zipped_bytes if bundle.zipped_bytes is not None else bundle.total_bytes
    label = "Zipped" if bundle.zipped_bytes is not None else "Total"
    evidence = (
        f"{format_bytes(measured)} zipped · {format_bytes(bundle.total_bytes)} uncompressed"
        if bundle.zipped_bytes is not None
        else f"{format_bytes(measured)} uncompressed"
    )

    if measured > limit:
        return [Check(
            id="size", severity="error", title="Exceeds size limit",
            detail=(
                f"{label} weight is {format_bytes(measured)}, over the "
                f"{format_bytes(limit)} limit for {rule.label}."
            ),
            suggestion="Compress images, subset fonts to used glyphs, and drop unused assets to get under the limit.",
            evidence=evidence,
        )]

    if bundle.zipped_bytes is not None and bundle.total_bytes > limit:
        return [Check(
            id="size", severity="warning", title="Uncompressed load exceeds limit",
            detail=(
                f"Packaged at {format_bytes(measured)} (under {format_bytes(limit)}), but the "
                f"uncompressed assets total {format_bytes(bundle.total_bytes)}. Networks that measure "
                "the uncompressed initial load (e.g. Amazon) may still reject it."
            ),
            suggestion="Reduce raw asset weight, not just the zip, to stay safe everywhere.",
            evidence=evidence,
        )]

    return [Check(
        id="size", severity="pass", title="Within size limit",
        detail=(
            f"{label} weight is {format_bytes(measured)}, under the "
            f"{format_bytes(limit)} limit for {rule.label}."
        ),
        evidence=evidence,
    )]


# ---- animation duration -----------------------------------------------------

def _round_duration(milliseconds: float) -> float:
    # JavaScript Math.round semantics for positive durations, to keep runner parity.
    return int(milliseconds / 100 + 0.5) * 100


def _format_duration(milliseconds: float) -> str:
    seconds = int(milliseconds / 100 + 0.5) / 10
    return f"{seconds:g}s"


def _animation_policy_description(rule: NetworkRule) -> str:
    policy = rule.animation_policy
    constraints = []
    if policy.get("maxAnimationDurationMs") is not None:
        constraints.append(
            f"maximum duration {_format_duration(policy['maxAnimationDurationMs'])}"
        )
    if policy.get("maxLoops") is not None:
        constraints.append(f"at most {policy['maxLoops']} loops")
    if policy.get("mustStopAfterLimit") and policy.get("maxAnimationDurationMs") is not None:
        constraints.append("animation must stop by that limit")

    if constraints:
        confidence = (
            "Documented policy"
            if policy.get("policyStatus") == "confirmed"
            else "Current conservative mapping"
        )
        return f"{confidence} for {rule.label}: {'; '.join(constraints)}."

    statuses = {
        "confirmed": "documented without a numeric value",
        "placement-specific": "placement-specific",
        "candidate": "not yet confirmed numerically",
        "qualitative": "qualitative rather than numeric",
        "unknown": "not numerically documented in the current mapping",
    }
    status = statuses.get(policy.get("policyStatus"), "not numerically documented")
    return (
        f"{rule.label} has no fixed numeric animation-duration limit in the current "
        f"ruleset; its policy is {status}."
    )


def animation_duration_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    policy = rule.animation_policy
    limit = policy.get("maxAnimationDurationMs")
    loop_limit = policy.get("maxLoops")
    policy_text = _animation_policy_description(rule)
    if limit is None and loop_limit is None:
        return [Check(
            id="animation-duration",
            severity="info",
            title="Animation duration policy",
            detail=(
                f"{policy_text} Check the selected placement's requirements if the banner animates."
            ),
        )]

    analysis = analyze_animation(bundle)
    confirmed = policy.get("policyStatus") == "confirmed"
    limit_text = _format_duration(limit) if limit is not None else None
    policy_verb = "limits" if confirmed else "may limit"
    report_evidence = "\n".join(analysis.evidence) or None

    if not analysis.detected:
        return [Check(
            id="animation-duration",
            severity="info",
            title="Animation duration policy",
            detail=(
                f"{policy_text} No statically measurable animation was found, but arbitrary "
                "JavaScript can still create one at runtime; verify the final creative manually."
            ),
        )]

    if analysis.infinite and (policy.get("mustStopAfterLimit") or loop_limit is not None):
        constraints = []
        if limit_text:
            constraints.append(f"stop after {limit_text}")
        if loop_limit is not None:
            constraints.append(f"run at most {loop_limit} loops")
        return [Check(
            id="animation-duration",
            severity="warning",
            title="Animation may not stop",
            detail=(
                f"Static inspection found an infinite animation, while {rule.label} {policy_verb} "
                f"creatives to {' and '.join(constraints)}."
            ),
            suggestion=(
                "Replace infinite repeats with a finite loop count and stop every animation/timer"
                + (f" by {limit_text}." if limit_text else " within the network limit.")
            ),
            evidence=report_evidence,
        )]

    measured = _round_duration(analysis.max_duration_ms) if analysis.max_duration_ms is not None else None
    duration_exceeded = measured is not None and limit is not None and measured > limit
    loops_exceeded = (
        analysis.max_loops is not None
        and loop_limit is not None
        and analysis.max_loops > loop_limit
    )

    if duration_exceeded or loops_exceeded:
        findings = []
        if duration_exceeded:
            findings.append(
                f"a {_format_duration(measured)} end time against a {_format_duration(limit)} limit"
            )
        if loops_exceeded:
            findings.append(f"{analysis.max_loops:g} iterations against a {loop_limit}-loop limit")
        return [Check(
            id="animation-duration",
            severity="warning",
            title="Animation exceeds the network policy",
            detail=(
                f"Static inspection found {' and '.join(findings)} for {rule.label}. "
                + (
                    "The network documents this limit."
                    if confirmed
                    else "The limit depends on placement or is conservatively mapped."
                )
            ),
            suggestion=(
                "Shorten the creative timeline"
                + (f" to {limit_text} or less" if limit_text else "")
                + (f" and use at most {loop_limit} loops" if loop_limit is not None else "")
                + "."
            ),
            evidence=report_evidence,
        )]

    if analysis.uncertain:
        measured_text = (
            ""
            if measured is None
            else f" The measurable portion ends at {_format_duration(measured)}, but other runtime animation remains."
        )
        return [Check(
            id="animation-duration",
            severity="info",
            title="Animation duration needs manual review",
            detail=(
                f"{policy_text} Animation code was found, but its complete end time cannot be proven without executing the banner."
                f"{measured_text} Confirm that animation for {rule.label} stops"
                + (f" by {limit_text}." if limit_text else " within the placement limit.")
            ),
            evidence=report_evidence,
        )]

    measured_text = (
        "Animation was detected."
        if measured is None
        else f"Animation ends at {_format_duration(measured)}."
    )
    loop_text = "" if analysis.max_loops is None else f" Longest declared repeat count: {analysis.max_loops:g}."
    return [Check(
        id="animation-duration",
        severity="info",
        title="Animation duration information",
        detail=(
            f"{policy_text} {measured_text}{loop_text} "
            "The statically measurable timing does not exceed this policy."
        ),
        evidence=report_evidence,
    )]


# ---- dimensions -------------------------------------------------------------

def _resolve_dimensions(bundle: BannerBundle):
    """(<size dict | None>, <source: 'meta'|'filename'|'css'|None>)."""
    from_meta = parse_ad_size(bundle.entry_html)
    if from_meta:
        return from_meta, "meta"
    for name in [bundle.entry_path, *[f.path for f in bundle.files]]:
        from_name = dimensions_from_name(name)
        if from_name:
            return from_name, "filename"
    from_css = dimensions_from_css(bundle.entry_html)
    if from_css:
        return from_css, "css"
    return None, None


def dimensions_validator(bundle: BannerBundle, _rule: NetworkRule) -> List[Check]:
    size, source = _resolve_dimensions(bundle)

    if not size:
        return [Check(
            id="dimensions", severity="warning", title="Dimensions unknown",
            detail="Could not determine the banner size from the meta tag, file names, or CSS.",
            suggestion='Add `<meta name="ad.size" content="width=W,height=H">` so the network reads the size reliably.',
        )]

    meta = parse_ad_size(bundle.entry_html)
    if source == "meta" and meta:
        name_hint = dimensions_from_name(bundle.entry_path)
        if name_hint and (name_hint["width"] != meta["width"] or name_hint["height"] != meta["height"]):
            return [Check(
                id="dimensions", severity="error", title="Dimension mismatch",
                detail=(
                    f"Meta ad.size is {meta['width']}×{meta['height']} but the file name "
                    f"suggests {name_hint['width']}×{name_hint['height']}."
                ),
                suggestion="Make the meta tag and the creative's intended size agree.",
                evidence=f"meta={meta['width']}x{meta['height']} name={name_hint['width']}x{name_hint['height']}",
            )]

    is_meta = source == "meta"
    return [Check(
        id="dimensions", severity="pass" if is_meta else "warning",
        title=f"Dimensions: {size['width']}×{size['height']}",
        detail=(
            f"Read {size['width']}×{size['height']} from the ad.size meta tag."
            if is_meta
            else f"Inferred {size['width']}×{size['height']} from the {source}; the network prefers an explicit meta tag."
        ),
        suggestion=(
            None
            if is_meta
            else f'Add `<meta name="ad.size" content="width={size["width"]},height={size["height"]}">` to make the size explicit.'
        ),
        evidence=f"{size['width']}x{size['height']} ({source})",
    )]


# ---- required tags ----------------------------------------------------------

def required_tags_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    checks: List[Check] = []
    html = bundle.entry_html
    haystack = _haystack(bundle)

    if rule.require_meta_ad_size:
        present = has_meta_ad_size(html)
        checks.append(Check(
            id="meta-ad-size", severity="pass" if present else "error",
            title="ad.size meta present" if present else "Missing ad.size meta",
            detail=(
                'The required `<meta name="ad.size">` tag is present.'
                if present
                else 'Networks read the creative size from `<meta name="ad.size" content="width=W,height=H">`.'
            ),
            suggestion=None if present else 'Add `<meta name="ad.size" content="width=300,height=250">` in the <head>.',
        ))

    for src in rule.required_scripts:
        present = src in haystack
        checks.append(Check(
            id=f"required-script:{src}",
            severity="pass" if present else "error",
            title="Vendor script present" if present else "Missing vendor script",
            detail=(
                f"Required {rule.label} script is included."
                if present
                else f"{rule.label} requires the script `{src}`."
            ),
            suggestion=None if present else f'Add <script src="https://{src}"></script>.',
            evidence=src,
        ))

    banner_click = has_banner_click(html)
    checks.append(Check(
        id="banner-click-element", severity="pass" if banner_click else "warning",
        title="Click wrapper present" if banner_click else "No #bannerClick element",
        detail=(
            'A clickable `id="bannerClick"` element is present.'
            if banner_click
            else 'Viewst exports wrap the creative in `<a id="bannerClick">`. Networks that bind the click by that id need it.'
        ),
        suggestion=None if banner_click else 'Wrap the creative in `<a id="bannerClick" href="#">…</a>`.',
    ))

    return checks


# ---- click integration ------------------------------------------------------

_FAMILY_LABEL = {
    "standard": "standard clickTag",
    "queryparam": "query-param clickTag",
    "sdk": "vendor SDK click",
    "macro": "ad-server macro",
    "none": "network-managed click",
}


def click_tag_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    haystack = _haystack(bundle)
    matches = bool(rule.click.signature.search(haystack))
    family = _FAMILY_LABEL.get(rule.click.family, rule.click.family)

    return [Check(
        id="click-integration", severity="pass" if matches else "warning",
        title="Click integration looks correct" if matches else "Click integration not detected",
        detail=(
            f"Found the expected {family} for {rule.label}."
            if matches
            else f"Could not find the {family} {rule.label} expects. The creative may not be clickable on this network."
        ),
        suggestion=None if matches else rule.click.suggestion,
        evidence=rule.click.signature_str,
    )]


# ---- click behavior ---------------------------------------------------------

_ANCHOR_RE = re.compile(r"""<a\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)
_ATTRIBUTE_RE = re.compile(r"""([^\s=<>/"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""")
_ACTIVE_ZONES_RE = re.compile(r"\bvar\s+activeZones\s*=\s*(?=\{)")
_HARDCODED_OPEN_RE = re.compile(r"""window\.open\(\s*["'](https?://[^"']+)["']""", re.I)

_ZONE_SEVERITY = {"forbidden": "error", "supported": "warning", "candidate": "warning", "unknown": "info"}


def _attributes(tag: str) -> dict:
    attrs = {}
    for match in _ATTRIBUTE_RE.finditer(tag[2:-1]):
        name = match.group(1).lower()
        if name not in attrs:
            attrs[name] = next((value for value in match.groups()[1:] if value is not None), "")
    return attrs


def _element_lookup(element_id: str) -> str:
    return r"""document\s*\.\s*getElementById\(\s*["']""" + re.escape(element_id) + r"""["']\s*\)"""


def _cancels_navigation(attrs: dict, haystack: str) -> bool:
    if re.search(r"^\s*(?:event\s*\.\s*preventDefault\s*\(\s*\)\s*;|return\s+false\s*;?\s*$)", attrs.get("onclick", "")):
        return True
    element_id = attrs.get("id")
    if not element_id:
        return False
    # Match immediate unconditional cancellation on this exact element only.
    handler = r"function\s*\(\s*([A-Za-z_$][\w$]*)\s*\)\s*\{\s*\1\s*\.\s*preventDefault\s*\(\s*\)\s*;"
    return bool(re.search(
        _element_lookup(element_id)
        + r"""\s*\.\s*(?:onclick\s*=\s*|addEventListener\(\s*["']click["']\s*,\s*)""" + handler,
        haystack,
    ))


def _has_click_zones(haystack: str) -> bool:
    # Viewst serializes the zone map as JSON. Never evaluate runtime expressions.
    decoder = json.JSONDecoder()
    for match in _ACTIVE_ZONES_RE.finditer(haystack):
        try:
            zones, _ = decoder.raw_decode(haystack, match.end())
        except ValueError:
            continue
        if isinstance(zones, dict) and any(
            isinstance(zone, dict) and isinstance(zone.get("clickURL"), str) and zone["clickURL"].strip()
            for zone in zones.values()
        ):
            return True
    return False


def _find_javascript_new_tab_anchor(haystack: str) -> Optional[str]:
    """An <a target="_blank"> whose href is a javascript: URL, set inline or by script."""
    for tag in _ANCHOR_RE.findall(haystack):
        attrs = _attributes(tag)
        if attrs.get("target", "").lower() != "_blank" or _cancels_navigation(attrs, haystack):
            continue
        if re.search(r"^\s*javascript:", attrs.get("href", ""), re.I):
            return tag
        element_id = attrs.get("id")
        if not element_id:
            continue
        script_href = re.compile(
            _element_lookup(element_id) + r"""\s*\.\s*href\s*=\s*["']\s*javascript:""",
        )
        if script_href.search(haystack):
            return tag
    return None


def _zone_detail(rule: NetworkRule) -> str:
    status = rule.click_zone_policy["status"]
    mechanism = rule.click_zone_policy.get("mechanism")
    scope = ("Only the zone area is clickable, and only on the scene where the zone is placed. "
             "Each zone opens its URL directly, bypassing the network click tag.")
    if status == "forbidden":
        return f"{rule.label} requires the whole banner to be a single click area. {scope}"
    if status == "supported":
        via = f" via {mechanism}" if mechanism else ""
        return f"{rule.label} accepts separate click areas{via}, so the network can't track these zones. {scope}"
    if status == "candidate":
        via = f" ({mechanism})" if mechanism else ""
        return (f"{rule.label} allows several landing pages{via}, but its spec doesn't say whether "
                f"separate click areas are accepted. {scope}")
    return f"No public {rule.label} spec confirms separate click areas. {scope}"


def click_behavior_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    """How the banner behaves on click: blank-tab links, click zones, hard-coded URLs."""
    checks: List[Check] = []
    haystack = _haystack(bundle)

    anchor = _find_javascript_new_tab_anchor(haystack)
    if anchor:
        checks.append(Check(
            id="click-javascript-new-tab", severity="error",
            title="Click opens a blank tab",
            detail=('A link combines a `javascript:` href with `target="_blank"`. Browsers run that script '
                    "in the new empty tab, where the click tag is undefined, so the click lands on about:blank."),
            suggestion=('Remove `target="_blank"` from the link, or open the click tag from an `onclick` '
                        "handler that calls `preventDefault()`."),
            evidence=re.sub(r"\s+", " ", anchor)[:160],
        ))

    if _has_click_zones(haystack):
        policy = rule.click_zone_policy
        status = policy["status"]
        mechanism = policy.get("mechanism")
        checks.append(Check(
            id="click-zones", severity=_ZONE_SEVERITY[status],
            title=("Click zones are not allowed on this network" if status == "forbidden"
                   else "Click zones bypass the click tag"),
            detail=_zone_detail(rule),
            suggestion=(
                "Remove the click zones and use one banner-wide click through the network click tag."
                if status == "forbidden" or not mechanism
                else f"Route each zone through {mechanism}, or remove the zones and use one banner-wide click."
            ),
            evidence=policy["source"],
        ))

    hardcoded = _HARDCODED_OPEN_RE.search(haystack)
    if hardcoded:
        checks.append(Check(
            id="click-hardcoded-url", severity="warning",
            title="Click opens a hard-coded URL",
            detail=(f"The banner opens a fixed URL instead of the {rule.label} click tag, "
                    "so the network can't override or count the click."),
            suggestion=rule.click.suggestion,
            evidence=hardcoded.group(1),
        ))

    return checks


# ---- external URLs ----------------------------------------------------------

_INERT_HOSTS = {"w3.org", "www.w3.org", "schema.org", "www.schema.org"}


def _host_matches(host: str, entry: str) -> bool:
    return host == entry or host.endswith(f".{entry}")


def _script_host(entry: str) -> str:
    return entry.split("/")[0].lower()


def external_url_validator(bundle: BannerBundle, rule: NetworkRule) -> List[Check]:
    expected = {_script_host(s) for s in rule.required_scripts}

    hosts = {}  # host -> example url (insertion-ordered)
    for _, text in all_text(bundle):
        for url in extract_urls(text):
            host = host_of(url)
            if not host or host in _INERT_HOSTS or host in expected:
                continue
            if host not in hosts:
                hosts[host] = url

    if rule.external_urls["mode"] == "whitelist":
        allow = rule.external_urls["allow"]
        violations = [(h, u) for h, u in hosts.items() if not any(_host_matches(h, a) for a in allow)]
        if not violations:
            return [Check(
                id="external-urls", severity="pass", title="No forbidden external references",
                detail=f"{rule.label} forbids non-allowlisted external sources, and none were found.",
            )]
        plural = "s" if len(violations) > 1 else ""
        return [Check(
            id="external-urls", severity="error",
            title=f"{len(violations)} forbidden external reference{plural}",
            detail=(
                f"{rule.label} rejects creatives that reference external hosts. Found: "
                f"{', '.join(h for h, _ in violations)}. Allowed: {', '.join(allow)}."
            ),
            suggestion="Download and bundle these assets locally (fonts/images/scripts) so no external URL remains in the HTML.",
            evidence="\n".join(u for _, u in violations),
        )]

    # allow mode
    if not hosts:
        return [Check(
            id="external-urls", severity="pass", title="No external references",
            detail="All assets are self-contained.",
        )]
    plural = "s" if len(hosts) > 1 else ""
    return [Check(
        id="external-urls", severity="info",
        title=f"{len(hosts)} external reference{plural}",
        detail=(
            f"{rule.label} permits external references — this is informational, your call. They must "
            f"stay reachable when the ad serves: {', '.join(hosts.keys())}."
        ),
        suggestion="Consider bundling fonts/images locally to avoid load failures or extra latency.",
        evidence="\n".join(hosts.values()),
    )]


# ---- registry ---------------------------------------------------------------

# Order here is the display order in the report.
_VALIDATORS = [
    structure_validator,
    size_validator,
    animation_duration_validator,
    dimensions_validator,
    required_tags_validator,
    click_tag_validator,
    click_behavior_validator,
    external_url_validator,
]


def _worst(checks: List[Check]) -> str:
    """info is purely informational and never elevates the verdict."""
    if any(c.severity == "error" for c in checks):
        return "error"
    if any(c.severity == "warning" for c in checks):
        return "warning"
    return "pass"


def validate(bundle: BannerBundle, rule: NetworkRule) -> ValidationReport:
    checks: List[Check] = []
    for v in _VALIDATORS:
        checks.extend(v(bundle, rule))
    return ValidationReport(
        network=rule,
        checks=checks,
        errors=sum(1 for c in checks if c.severity == "error"),
        warnings=sum(1 for c in checks if c.severity == "warning"),
        infos=sum(1 for c in checks if c.severity == "info"),
        passed=sum(1 for c in checks if c.severity == "pass"),
        verdict=_worst(checks),
    )
