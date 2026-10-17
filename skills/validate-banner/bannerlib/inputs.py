"""Input adapters: raw .zip bytes or a single HTML file -> BannerBundle, plus
best-effort network auto-detection. Ports src/lib/input/{zipAdapter,fileAdapter,
detectNetwork}.ts using stdlib zipfile (no JSZip needed)."""
import io
import zipfile
from typing import List, Optional

from .bundle import BannerBundle, BannerFile, build_bundle, is_text_path
from .rules import visible_networks


# Zip-bomb guard (port of src/lib/input/zipLimits.ts — keep limits + messages in
# sync). Checked against the central directory before any entry is inflated;
# zipfile never inflates past an entry's declared file_size (it fails the CRC).
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_ENTRY_BYTES = 20 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024


class ZipLimitError(ValueError):
    pass


def assert_zip_within_limits(uncompressed_sizes: List[int]) -> None:
    if len(uncompressed_sizes) > MAX_ZIP_ENTRIES:
        raise ZipLimitError(
            f"ZIP rejected: more than {MAX_ZIP_ENTRIES} entries — not a plausible banner."
        )
    total = 0
    for size in uncompressed_sizes:
        if size > MAX_ZIP_ENTRY_BYTES:
            raise ZipLimitError("ZIP rejected: an entry exceeds 20 MB uncompressed.")
        total += size
    if total > MAX_ZIP_TOTAL_BYTES:
        raise ZipLimitError("ZIP rejected: total uncompressed size exceeds 50 MB.")


def _is_junk(path: str) -> bool:
    return (
        path.startswith("__MACOSX/")
        or path.endswith("/.DS_Store")
        or path.endswith(".DS_Store")
        or "/.git/" in path
    )


def _decode(data: bytes) -> str:
    # JS TextDecoder("utf-8") is lenient: invalid bytes -> U+FFFD.
    return data.decode("utf-8", errors="replace")


def bundle_from_zip_bytes(data: bytes, zipped_bytes: int) -> BannerBundle:
    """Parse raw .zip bytes into a normalized BannerBundle."""
    files: List[BannerFile] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert_zip_within_limits([i.file_size for i in zf.infolist() if not i.is_dir()])
        for info in zf.infolist():
            if info.is_dir():
                continue
            path = info.filename.replace("\\", "/")
            if _is_junk(path):
                continue
            content = zf.read(info)
            is_text = is_text_path(path)
            files.append(
                BannerFile(
                    path=path,
                    bytes=len(content),
                    is_text=is_text,
                    text=_decode(content) if is_text else None,
                    data=content,
                )
            )
    return build_bundle("zip", files, zipped_bytes)


def bundle_from_file_bytes(name: str, raw: bytes) -> BannerBundle:
    """Wrap single-HTML-file bytes as a one-file BannerBundle."""
    path = name or "index.html"
    return build_bundle(
        "file",
        [BannerFile(path=path, bytes=len(raw), is_text=True, text=_decode(raw), data=raw)],
    )


def detect_network(bundle: BannerBundle) -> Optional[str]:
    """Auto-detect the network from vendor <script src> markers (requiredScripts).
    Longer, more specific substrings win ties. Networks without markers (Google,
    Yandex, default) are not auto-detectable. Port of detectNetwork.ts."""
    haystack = "\n".join(
        f.text for f in bundle.files if f.is_text and isinstance(f.text, str)
    ).lower()
    if not haystack:
        return None

    best = None  # (id, score)
    for rule in visible_networks():
        score = 0
        for sub in rule.required_scripts:
            if sub and sub.lower() in haystack:
                score += len(sub)
        if score > 0 and (best is None or score > best[1]):
            best = (rule.id, score)
    return best[0] if best else None
