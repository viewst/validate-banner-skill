"""The normalized BannerBundle every validator consumes, regardless of whether the
banner arrived as a ZIP or a single HTML file. Port of src/lib/input/bundle.ts.

Note: TS `BannerFile.blob` (a Blob) is `BannerFile.data` (raw bytes) here."""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_TEXT_EXTENSIONS = {"html", "htm", "js", "mjs", "css", "json", "svg", "txt", "xml", "csv"}


@dataclass
class BannerFile:
    path: str  # normalized, forward-slash, relative
    bytes: int  # uncompressed size
    is_text: bool
    text: Optional[str] = None  # decoded UTF-8 for text assets
    data: Optional[bytes] = None  # raw bytes (TS `blob`)


@dataclass
class BannerBundle:
    source: str  # "zip" | "file" | "paste"
    files: List[BannerFile]
    entry_html: str
    entry_path: str
    total_bytes: int
    zipped_bytes: Optional[int] = None  # original .zip size when source == "zip"
    warnings: List[str] = field(default_factory=list)


def ext_of(path: str) -> str:
    i = path.rfind(".")
    return "" if i == -1 else path[i + 1:].lower()


def base_name(path: str) -> str:
    norm = path.replace("\\", "/")
    return norm[norm.rfind("/") + 1:]


def is_text_path(path: str) -> bool:
    return ext_of(path) in _TEXT_EXTENSIONS


def _depth(path: str) -> int:
    return path.count("/")


def _is_html_path(path: str) -> bool:
    return ext_of(path) in ("html", "htm")


def _entry_rank(f: BannerFile) -> int:
    """0 = literally index.html, 1 = any other name (lower ranks first)."""
    return 0 if base_name(f.path).lower() == "index.html" else 1


def _better_entry(a: BannerFile, b: BannerFile) -> bool:
    if _entry_rank(a) != _entry_rank(b):
        return _entry_rank(a) < _entry_rank(b)
    if _depth(a.path) != _depth(b.path):
        return _depth(a.path) < _depth(b.path)
    return a.path < b.path  # ASCII paths: < matches localeCompare


def _resolve_entry(files: List[BannerFile]) -> Optional[BannerFile]:
    best: Optional[BannerFile] = None
    for f in files:
        if not _is_html_path(f.path):
            continue
        if best is None or _better_entry(f, best):
            best = f
    return best


def build_bundle(source: str, files: List[BannerFile], zipped_bytes: Optional[int] = None) -> BannerBundle:
    warnings: List[str] = []
    total_bytes = sum(f.bytes for f in files)
    entry = _resolve_entry(files)
    if entry is None:
        warnings.append("No HTML entry file (index.html) was found in the upload.")
    html_count = sum(1 for f in files if _is_html_path(f.path))
    if html_count > 1:
        warnings.append(
            f'Multiple HTML files found ({html_count}); validating '
            f'"{entry.path if entry else None}" as the entry point.'
        )
    return BannerBundle(
        source=source,
        files=files,
        entry_html=(entry.text or "") if entry else "",
        entry_path=entry.path if entry else "",
        total_bytes=total_bytes,
        zipped_bytes=zipped_bytes,
        warnings=warnings,
    )


def find_by_name(bundle: BannerBundle, name: str) -> Optional[BannerFile]:
    """Case-insensitive lookup by basename (special-file checks)."""
    target = name.lower()
    for f in bundle.files:
        if base_name(f.path).lower() == target:
            return f
    return None


def all_text(bundle: BannerBundle) -> List[Tuple[str, str]]:
    """(path, text) pairs for every text asset — used by the URL scanner/fix."""
    return [(f.path, f.text) for f in bundle.files if f.is_text and isinstance(f.text, str)]
