"""Loads the generated rules.json (single source of truth: src/rules/networks.ts)
into NetworkRule objects with compiled click signatures. Port of the data shape in
src/rules/{types,networks}.ts. rules.json is resolved relative to THIS file so the
skill works wherever its folder is copied/symlinked (never depends on cwd)."""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# rules.json lives in the skill dir (parent of bannerlib/).
_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.json")


@dataclass
class ClickIntegration:
    family: str
    signature: "re.Pattern"
    suggestion: str
    signature_str: str  # JS String(RegExp) form, "/source/flags", for evidence


@dataclass
class SpecialFile:
    name: str
    required: bool
    note: Optional[str] = None


@dataclass
class NetworkRule:
    id: str
    label: str
    group: str
    size_limit_bytes: int
    require_meta_ad_size: bool
    required_scripts: List[str]
    click: ClickIntegration
    special_files: List[SpecialFile]
    external_urls: dict  # {"mode":"allow"} | {"mode":"whitelist","allow":[...],"sdkRequired":?}
    disable_click_url_change: bool
    hidden: bool = False
    notes: Optional[str] = None


def _compile_regex(rx: dict) -> "re.Pattern":
    source = rx["__regex__"]["source"]
    flags = rx["__regex__"]["flags"]
    f = 0
    if "i" in flags:
        f |= re.I
    if "m" in flags:
        f |= re.M
    if "s" in flags:
        f |= re.S
    return re.compile(source, f)


def _signature_str(rx: dict) -> str:
    return f"/{rx['__regex__']['source']}/{rx['__regex__']['flags']}"


def _parse_rule(n: dict) -> NetworkRule:
    click = n["click"]
    return NetworkRule(
        id=n["id"],
        label=n["label"],
        group=n["group"],
        size_limit_bytes=n["sizeLimitBytes"],
        require_meta_ad_size=n["requireMetaAdSize"],
        required_scripts=list(n["requiredScripts"]),
        click=ClickIntegration(
            family=click["family"],
            signature=_compile_regex(click["signature"]),
            suggestion=click["suggestion"],
            signature_str=_signature_str(click["signature"]),
        ),
        special_files=[
            SpecialFile(name=sf["name"], required=sf["required"], note=sf.get("note"))
            for sf in n.get("specialFiles", [])
        ],
        external_urls=n["externalUrls"],
        disable_click_url_change=n["disableClickUrlChange"],
        hidden=bool(n.get("hidden", False)),
        notes=n.get("notes"),
    )


def _load() -> List[NetworkRule]:
    with open(_RULES_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return [_parse_rule(n) for n in raw]


NETWORKS: List[NetworkRule] = _load()
_BY_ID: Dict[str, NetworkRule] = {n.id: n for n in NETWORKS}


def get_network(network_id: str) -> Optional[NetworkRule]:
    return _BY_ID.get(network_id)


def visible_networks() -> List[NetworkRule]:
    """Networks shown by default (hidden = legacy/internal)."""
    return [n for n in NETWORKS if not n.hidden]
