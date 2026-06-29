"""Lightweight, regex-based HTML probes. Port of src/lib/html.ts. Regex (not a DOM
parser) keeps these robust against malformed banner markup and identical to the
browser/Node engine."""
import re
from typing import Optional

AdSize = dict  # {"width": int, "height": int}

# <meta name="ad.size" content="width=W,height=H"> in either attribute order.
_AD_SIZE_RE_1 = re.compile(
    r"""<meta[^>]*name=["']ad\.size["'][^>]*content=["']([^"']+)["']""", re.I
)
_AD_SIZE_RE_2 = re.compile(
    r"""<meta[^>]*content=["']([^"']+)["'][^>]*name=["']ad\.size["']""", re.I
)
_WIDTH_RE = re.compile(r"width\s*=\s*(\d+)", re.I)
_HEIGHT_RE = re.compile(r"height\s*=\s*(\d+)", re.I)

_HAS_META_AD_SIZE_RE = re.compile(r"""<meta[^>]*name=["']ad\.size["']""", re.I)
_HAS_BANNER_CLICK_RE = re.compile(r"""id=["']bannerClick["']""", re.I)

_CSS_WIDTH_RE = re.compile(r"width\s*:\s*(\d{2,4})px", re.I)
_CSS_HEIGHT_RE = re.compile(r"height\s*:\s*(\d{2,4})px", re.I)
_NAME_DIM_RE = re.compile(r"(\d{2,4})\s*[xX×]\s*(\d{2,4})")

# Same URL shape the external-URL scanner / fix use.
_URL_RE = re.compile(r"""https?://[^\s"'`)<>\\]+""", re.I)
_TRAILING_PUNCT_RE = re.compile(r"[.,;]+$")
_HOST_RE = re.compile(r"^https?://([^/?#]*)", re.I)


def parse_ad_size(html: str) -> Optional[AdSize]:
    m = _AD_SIZE_RE_1.search(html) or _AD_SIZE_RE_2.search(html)
    if not m:
        return None
    content = m.group(1)
    w = _WIDTH_RE.search(content)
    h = _HEIGHT_RE.search(content)
    if w and h:
        return {"width": int(w.group(1)), "height": int(h.group(1))}
    return None


def has_meta_ad_size(html: str) -> bool:
    return bool(_HAS_META_AD_SIZE_RE.search(html))


def has_banner_click(html: str) -> bool:
    return bool(_HAS_BANNER_CLICK_RE.search(html))


def dimensions_from_css(html: str) -> Optional[AdSize]:
    w = _CSS_WIDTH_RE.search(html)
    h = _CSS_HEIGHT_RE.search(html)
    if w and h:
        return {"width": int(w.group(1)), "height": int(h.group(1))}
    return None


def dimensions_from_name(name: str) -> Optional[AdSize]:
    m = _NAME_DIM_RE.search(name)
    if m:
        return {"width": int(m.group(1)), "height": int(m.group(2))}
    return None


def extract_urls(text: str) -> list:
    """Distinct external http(s) references, trailing punctuation stripped."""
    return [_TRAILING_PUNCT_RE.sub("", u) for u in _URL_RE.findall(text)]


def host_of(url: str) -> str:
    """Authority (host[:port], lowercased, userinfo stripped) — matches JS URL.host
    for well-formed URLs, with a regex fallback for anything unusual."""
    m = _HOST_RE.match(url)
    if not m:
        return url.lower()
    authority = m.group(1)
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return authority.lower()
