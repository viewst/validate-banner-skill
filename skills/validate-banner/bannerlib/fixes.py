"""Auto-fixes mirroring the web tool: strip non-whitelisted external URLs
(whitelist-mode networks, e.g. Amazon DSP) and compress raster images to fit the
size limit. Ports src/lib/fix/{externalUrlFix,compressToFit,encodeImageNode,pack}.ts.

Image re-encoding needs Pillow (the only non-stdlib dependency, OPTIONAL). When
Pillow is absent, PIL_AVAILABLE is False and compress_to_fit must not be called;
the CLI degrades gracefully (URL strip still runs)."""
import io
import re
import zipfile
from dataclasses import dataclass, replace
from typing import Callable, List, Optional

from .bundle import BannerBundle, BannerFile, build_bundle, ext_of
from .htmlprobe import host_of
from .rules import NetworkRule

try:  # Pillow is optional — only the compress-to-fit step uses it.
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    Image = None
    PIL_AVAILABLE = False


# ---- external-URL strip -----------------------------------------------------

_INERT_HOSTS = {"w3.org", "www.w3.org", "schema.org", "www.schema.org"}
_URL_RE = re.compile(r"""https?://[^\s"'`)<>\\]+""", re.I)
_TRAILING_PUNCT_RE = re.compile(r"[.,;]+$")


def _host_matches(host: str, entry: str) -> bool:
    return host == entry or host.endswith(f".{entry}")


def _script_host(entry: str) -> str:
    return entry.split("/")[0].lower()


@dataclass
class FixResult:
    bundle: BannerBundle
    removed: List[str]
    kept: List[str]
    changed_files: List[str]


def apply_external_url_fix(bundle: BannerBundle, rule: NetworkRule) -> FixResult:
    """Blank every non-whitelisted external URL in place. No-op for allow-mode."""
    if rule.external_urls.get("mode") != "whitelist":
        return FixResult(bundle=bundle, removed=[], kept=[], changed_files=[])

    allow = rule.external_urls["allow"]
    expected = {_script_host(s) for s in rule.required_scripts}
    removed = dict()  # ordered set
    kept = dict()
    changed_files: List[str] = []

    def classify(url: str) -> str:
        host = host_of(url)
        if not host or host in _INERT_HOSTS:
            return "inert"
        if host in expected or any(_host_matches(host, a) for a in allow):
            return "keep"
        return "strip"

    files: List[BannerFile] = []
    for f in bundle.files:
        if not f.is_text or not isinstance(f.text, str):
            files.append(f)
            continue
        changed = False

        def repl(m, _changed=lambda: None):
            nonlocal changed
            match = m.group(0)
            url = _TRAILING_PUNCT_RE.sub("", match)
            verdict = classify(url)
            if verdict == "strip":
                removed[url] = True
                changed = True
                return ""
            if verdict == "keep":
                kept[url] = True
            return match

        next_text = _URL_RE.sub(repl, f.text)
        if not changed:
            files.append(f)
            continue
        changed_files.append(f.path)
        files.append(replace(f, text=next_text, bytes=len(next_text.encode("utf-8"))))

    return FixResult(
        bundle=build_bundle(bundle.source, files, bundle.zipped_bytes),
        removed=list(removed.keys()),
        kept=list(kept.keys()),
        changed_files=changed_files,
    )


# ---- zip repack -------------------------------------------------------------

def pack_zip(bundle: BannerBundle) -> bytes:
    """Repack a bundle into .zip bytes. Uses DEFLATE (stdlib zlib) — more faithful
    to how networks weigh real uploads than the Node tool's STORE default."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.files:
            if f.is_text and isinstance(f.text, str):
                zf.writestr(f.path, f.text)
            elif f.data is not None:
                zf.writestr(f.path, f.data)
    return buf.getvalue()


# ---- raster re-encode (Pillow) ----------------------------------------------

_JPEG_FLOOR, _JPEG_CEIL = 40, 95
_PNG_MIN_COLORS, _PNG_MAX_COLORS = 32, 256


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def raster_format(path: str) -> Optional[str]:
    e = ext_of(path)
    if e in ("jpg", "jpeg"):
        return "jpeg"
    if e == "png":
        return "png"
    if e == "webp":
        return "webp"
    return None


def encode_raster(data: bytes, fmt: str, t: float) -> bytes:
    """Re-encode raster bytes at effort t in [0,1], preserving format. Requires
    Pillow. WebP is returned unchanged (no quality re-encode applied)."""
    if fmt == "webp" or not PIL_AVAILABLE:
        return data
    img = Image.open(io.BytesIO(data))
    out = io.BytesIO()
    if fmt == "jpeg":
        quality = round(_lerp(_JPEG_FLOOR, _JPEG_CEIL, t))
        img.convert("RGB").save(out, format="JPEG", quality=quality)
        return out.getvalue()
    if fmt == "png":
        colors = round(_lerp(_PNG_MIN_COLORS, _PNG_MAX_COLORS, t))
        # FASTOCTREE supports alpha; MEDIANCUT (default) does not.
        quant = img.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
        quant.save(out, format="PNG", optimize=True)
        return out.getvalue()
    return data


def has_compressible_images(bundle: BannerBundle) -> bool:
    return any(
        (not f.is_text) and f.data is not None and raster_format(f.path) is not None
        for f in bundle.files
    )


# ---- compress-to-fit (binary search) ----------------------------------------

@dataclass
class CompressResult:
    bundle: BannerBundle
    fit: bool
    quality: float
    before_bytes: int
    after_bytes: int
    limit_bytes: int
    changed: List[dict]
    skipped: List[dict]


_ITERATIONS = 7


def compress_to_fit(
    bundle: BannerBundle,
    rule: NetworkRule,
    encode: Callable[[bytes, str, float], bytes] = encode_raster,
    pack: Callable[[BannerBundle], bytes] = pack_zip,
) -> CompressResult:
    """Binary-search the highest effort t whose repacked archive fits the limit.
    Port of compressToFit.ts (7 iterations, keep-smaller-of-original-vs-encoded)."""
    limit = rule.size_limit_bytes
    before_bytes = len(pack(bundle))

    skipped = [
        {"path": f.path, "reason": "animated GIF can't be auto-compressed"}
        for f in bundle.files
        if (not f.is_text) and ext_of(f.path) == "gif"
    ]

    has_targets = any(
        (not f.is_text) and f.data is not None and raster_format(f.path)
        for f in bundle.files
    )
    if not has_targets:
        return CompressResult(
            bundle=bundle, fit=before_bytes <= limit, quality=1,
            before_bytes=before_bytes, after_bytes=before_bytes, limit_bytes=limit,
            changed=[], skipped=skipped,
        )

    def candidate_at(t: float):
        changed: List[dict] = []
        files: List[BannerFile] = []
        for f in bundle.files:
            fmt = raster_format(f.path) if ((not f.is_text) and f.data is not None) else None
            if not fmt or f.data is None:
                files.append(f)
                continue
            enc = encode(f.data, fmt, t)
            if len(enc) >= f.bytes:  # never enlarge
                files.append(f)
                continue
            changed.append({"path": f.path, "before": f.bytes, "after": len(enc)})
            files.append(replace(f, data=enc, bytes=len(enc), text=None))
        rebuilt = build_bundle(bundle.source, files, bundle.zipped_bytes)
        packed = len(pack(rebuilt))
        # sizeValidator gates on zipped_bytes for zip uploads — keep it truthful.
        fixed = replace(rebuilt, zipped_bytes=packed) if bundle.source == "zip" else rebuilt
        return {"bundle": fixed, "packed": packed, "changed": changed}

    lo, hi = 0.0, 1.0
    best = None
    for _ in range(_ITERATIONS):
        t = (lo + hi) / 2
        c = candidate_at(t)
        if c["packed"] <= limit:
            best = {**c, "t": t}
            lo = t  # room to raise quality
        else:
            hi = t  # must compress harder
    if best is None:  # nothing fit during the search -> max compression
        best = {**candidate_at(0.0), "t": 0.0}

    return CompressResult(
        bundle=best["bundle"], fit=best["packed"] <= limit, quality=best["t"],
        before_bytes=before_bytes, after_bytes=best["packed"], limit_bytes=limit,
        changed=best["changed"], skipped=skipped,
    )
