"""Static animation timing analysis. Port of src/lib/animation.ts.

The analyzer never executes banner code. Viewst exports expose a JSON scene model,
literal CSS/SVG timing can be measured, and dynamic JavaScript is marked uncertain.
"""
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from .bundle import BannerBundle, all_text, ext_of


@dataclass
class AnimationAnalysis:
    detected: bool
    max_duration_ms: Optional[float] = None
    max_loops: Optional[float] = None
    infinite: bool = False
    uncertain: bool = False
    evidence: List[str] = field(default_factory=list)


@dataclass
class _Observation:
    evidence: str
    duration_ms: Optional[float] = None
    loops: Optional[float] = None
    infinite: bool = False
    uncertain: bool = False


DEFAULT_SCENE_DURATION_SECONDS = 5
DEFAULT_TYPEWRITER_STAGGER_SECONDS = 0.05
MIN_TRANSITION_DURATION_SECONDS = 0.001
MAX_TRANSITION_DURATION_SECONDS = 10
MAX_EVIDENCE = 6


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _finite_number(value: Any) -> Optional[float]:
    # bool is a subclass of int in Python, but it is not a JS number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_number(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _format_seconds(milliseconds: float) -> str:
    seconds = int(milliseconds / 100 + 0.5) / 10
    return f"{seconds:g}s"


# ---- Viewst/Wizard exports --------------------------------------------------

def _assigned_json_array(text: str, name: str) -> Tuple[bool, Optional[list]]:
    match = re.search(rf"\b(?:let|const|var)\s+{re.escape(name)}\s*=\s*", text)
    if not match:
        return False, None
    start = match.end()
    if start >= len(text) or text[start] != "[":
        return True, None

    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                    return True, parsed if isinstance(parsed, list) else None
                except (TypeError, ValueError):
                    return True, None
    return True, None


def _meta_number(animation: dict, key: str) -> Optional[float]:
    meta = animation.get("metaDescription")
    field_value = meta.get(key) if isinstance(meta, dict) else None
    if not isinstance(field_value, dict):
        return None
    value = _finite_number(field_value.get("value"))
    return value if value is not None else _finite_number(field_value.get("defaultValue"))


def _normalize_stagger(value: Any) -> Optional[float]:
    direct = _finite_number(value)
    if direct is not None:
        return direct
    return _finite_number(value.get("each")) if isinstance(value, dict) else None


def _typewriter_stagger(animation: dict) -> float:
    meta = _meta_number(animation, "stagger")
    if meta is not None:
        return meta
    description = animation.get("description")
    description = description if isinstance(description, dict) else {}
    from_to = description.get("fromTo") if isinstance(description.get("fromTo"), dict) else {}
    candidates = [
        from_to.get("to", {}).get("stagger") if isinstance(from_to.get("to"), dict) else None,
        description.get("to", {}).get("stagger") if isinstance(description.get("to"), dict) else None,
        description.get("from", {}).get("stagger") if isinstance(description.get("from"), dict) else None,
    ]
    for candidate in candidates:
        stagger = _normalize_stagger(candidate)
        if stagger is not None:
            return stagger
    return DEFAULT_TYPEWRITER_STAGGER_SECONDS


_NAMED_HTML_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": "\u00a0", "ensp": "\u2002", "emsp": "\u2003", "thinsp": "\u2009",
}


def _decoded_text_length(item: dict) -> int:
    fragments = item.get("textFragments")
    if isinstance(fragments, dict):
        max_end = 0
        for fragment in fragments.values():
            end = _finite_number(fragment.get("endIndex")) if isinstance(fragment, dict) else None
            max_end = max(max_end, int(end or 0))
        if max_end > 0:
            return max_end
    if item.get("text") is None:
        return 0
    plain = re.sub(r"<[^>]*>", "", str(item.get("text")))

    def code_point(match, base):
        try:
            return chr(int(match.group(1), base))
        except (ValueError, OverflowError):
            return match.group(0)

    plain = re.sub(r"&#x([0-9a-f]+);", lambda m: code_point(m, 16), plain, flags=re.I)
    plain = re.sub(r"&#(\d+);", lambda m: code_point(m, 10), plain)
    plain = re.sub(
        r"&([a-z0-9]+);",
        lambda m: _NAMED_HTML_ENTITIES.get(m.group(1).lower(), m.group(0)),
        plain,
        flags=re.I,
    )
    plain = re.sub(r"&nbsp(?!;)", "\u00a0", plain, flags=re.I)
    return len(plain)


def _animation_visible_duration(animation: dict, text_length: Optional[int]) -> float:
    duration = _finite_number(animation.get("duration")) or 0
    config = animation.get("textAnimationConfig")
    if isinstance(config, dict) and config.get("typewriter"):
        duration = 0.001 if text_length is None else _typewriter_stagger(animation) * text_length
    repeat = _meta_number(animation, "repeat")
    repeat_delay = _meta_number(animation, "repeatDelay")
    if repeat is not None and repeat_delay is not None and repeat > 0:
        return (repeat + 1) * duration + repeat_delay * repeat
    return duration


def _item_duration(item: dict) -> float:
    raw_animations = item.get("animations")
    animations = [
        value for value in raw_animations if isinstance(value, dict)
    ] if isinstance(raw_animations, list) else []
    has_text_animation = any(
        isinstance(animation.get("textAnimationConfig"), dict)
        or (
            isinstance(animation.get("name"), str)
            and (animation["name"].startswith("char") or animation["name"].startswith("word"))
        )
        for animation in animations
    )
    text_length = _decoded_text_length(item) if has_text_animation else None
    return max(
        [
            (_finite_number(animation.get("delay")) or 0)
            + _animation_visible_duration(animation, text_length)
            for animation in animations
        ]
        or [0]
    )


def _normalized_transition_duration(raw: Any) -> float:
    duration = _finite_number(raw)
    if duration is None or duration <= 0:
        return 0
    return min(MAX_TRANSITION_DURATION_SECONDS, max(MIN_TRANSITION_DURATION_SECONDS, duration))


def _compressed_transitions(scenes: list) -> Dict[str, float]:
    compressed: Dict[str, float] = {}
    for index in range(1, len(scenes) - 1):
        scene = scenes[index]
        next_scene = scenes[index + 1]
        incoming = scene.get("transition")
        outgoing = next_scene.get("transition")
        if not isinstance(incoming, dict) or not isinstance(outgoing, dict):
            continue
        if incoming.get("type") == "instant" or outgoing.get("type") == "instant":
            continue
        if incoming.get("type") == "fade" or outgoing.get("type") == "fade":
            continue
        in_duration = compressed.get(scene["scene_id"], _normalized_transition_duration(incoming.get("duration")))
        out_duration = compressed.get(next_scene["scene_id"], _normalized_transition_duration(outgoing.get("duration")))
        total = in_duration + out_duration
        if total > scene["duration"] and scene["duration"] > 0:
            ratio = scene["duration"] / total
            compressed[scene["scene_id"]] = max(MIN_TRANSITION_DURATION_SECONDS, in_duration * ratio)
            compressed[next_scene["scene_id"]] = max(MIN_TRANSITION_DURATION_SECONDS, out_duration * ratio)
    return compressed


def _viewst_duration(items_value: list, scenes_value: list) -> Optional[float]:
    items = [item for item in items_value if isinstance(item, dict)]
    raw_scenes = [scene for scene in scenes_value if isinstance(scene, dict)]
    if not raw_scenes:
        return None
    items_by_id = {
        item["creativeId"]: item
        for item in items
        if isinstance(item.get("creativeId"), str)
    }
    scenes = []
    for index, scene in enumerate(raw_scenes):
        explicit = _positive_number(scene.get("sceneDuration"))
        ids = scene.get("items") if isinstance(scene.get("items"), list) else []
        fallback = max([
            _item_duration(items_by_id[item_id])
            for item_id in ids
            if isinstance(item_id, str) and item_id in items_by_id
        ] or [0])
        scenes.append({
            "scene_id": scene.get("sceneId") if isinstance(scene.get("sceneId"), str) else f"scene-{index}",
            "duration": explicit if explicit is not None else (fallback if fallback > 0 else DEFAULT_SCENE_DURATION_SECONDS),
            "transition": scene.get("transition") if isinstance(scene.get("transition"), dict) else None,
        })

    compressed = _compressed_transitions(scenes)
    cumulative_start = 0.0
    max_end = 0.0
    for index, scene in enumerate(scenes):
        max_end = max(max_end, cumulative_start + scene["duration"])
        next_scene = scenes[index + 1] if index + 1 < len(scenes) else None
        transition = next_scene.get("transition") if next_scene else None
        effective = 0
        if next_scene:
            effective = compressed.get(next_scene["scene_id"], _finite_number((transition or {}).get("duration")) or 0)
        overlap = 0
        if transition and transition.get("type") != "instant" and effective > 0:
            overlap = min(
                scene["duration"],
                min(MAX_TRANSITION_DURATION_SECONDS, max(MIN_TRANSITION_DURATION_SECONDS, effective)),
            )
        cumulative_start += scene["duration"] - overlap
    return max(0, max_end) * 1000


def _viewst_loops(items_value: list) -> Optional[float]:
    result = None
    for item in [value for value in items_value if isinstance(value, dict)]:
        raw_animations = item.get("animations")
        animations = [
            value for value in raw_animations if isinstance(value, dict)
        ] if isinstance(raw_animations, list) else []
        for animation in animations:
            repeat = _meta_number(animation, "repeat")
            if repeat is not None and repeat > 0:
                result = max(result or 1, repeat + 1)
    return result


def _viewst_observations(path: str, text: str) -> List[_Observation]:
    if not re.search(r"new\s+UniversalAnimationEngine\s*\(", text):
        return []
    items_found, items = _assigned_json_array(text, "items")
    scenes_found, scenes = _assigned_json_array(text, "scenes")
    if not items_found or not scenes_found:
        return []
    if items is None or scenes is None:
        return [_Observation(
            uncertain=True,
            evidence=f"{path}: Viewst animation model found, but its timing data could not be parsed",
        )]
    duration = _viewst_duration(items, scenes)
    if duration is None:
        return [_Observation(
            uncertain=True,
            evidence=f"{path}: Viewst animation model has no readable scenes",
        )]
    return [_Observation(
        duration_ms=duration,
        loops=_viewst_loops(items),
        evidence=f"{path}: Viewst scene model ends at {_format_seconds(duration)}",
    )]


# ---- CSS animations ---------------------------------------------------------

def _split_top_level(value: str, separator: str) -> List[str]:
    result = []
    start = 0
    quote = ""
    escaped = False
    depth = 0
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            part = value[start:index].strip()
            if part:
                result.append(part)
            start = index + 1
    part = value[start:].strip()
    if part:
        result.append(part)
    return result


def _split_whitespace(value: str) -> List[str]:
    result = []
    start = None
    quote = ""
    escaped = False
    depth = 0
    for index in range(len(value) + 1):
        char = value[index] if index < len(value) else " "
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
            if start is None:
                start = index
        elif char in "([":
            depth += 1
            if start is None:
                start = index
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char.isspace() and depth == 0:
            if start is not None:
                result.append(value[start:index])
            start = None
        elif start is None:
            start = index
    return result


def _css_time(value: str) -> Optional[float]:
    token = value.strip().lower()
    if token in ("0", "+0", "-0"):
        return 0
    match = re.fullmatch(r"(-?(?:\d+(?:\.\d+)?|\.\d+))(ms|s)", token)
    if not match:
        return None
    amount = float(match.group(1))
    return amount * 1000 if match.group(2) == "s" else amount


def _css_variables(texts: List[str]) -> Tuple[Dict[str, str], set]:
    values: Dict[str, str] = {}
    ambiguous = set()
    for text in texts:
        for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+)", text):
            name, value = match.group(1), match.group(2).strip()
            if name in values and values[name] != value:
                ambiguous.add(name)
            elif name not in ambiguous:
                values[name] = value
    return values, ambiguous


def _css_source(path: str, text: str) -> str:
    if ext_of(path) == "css":
        return text
    if ext_of(path) not in ("html", "htm", "svg"):
        return ""
    fragments = [
        match.group(1)
        for match in re.finditer(r"<style\b[^>]*>([\s\S]*?)</style\s*>", text, re.I)
    ]
    fragments.extend(
        f":inline {{ {match.group(2)} }}"
        for match in re.finditer(r"\bstyle\s*=\s*([\"'])([\s\S]*?)\1", text, re.I)
    )
    return "\n".join(fragments)


def _resolve_css_variables(value: str, variables: Tuple[Dict[str, str], set]) -> Optional[str]:
    values, ambiguous = variables
    resolved = value
    pattern = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]+))?\)")
    for _ in range(5):
        if "var(" not in resolved:
            return resolved
        failed = False

        def replacement(match):
            nonlocal failed
            name, fallback = match.group(1), match.group(2)
            if name not in ambiguous and name in values:
                return values[name]
            if fallback is not None:
                return fallback.strip()
            failed = True
            return match.group(0)

        resolved = pattern.sub(replacement, resolved)
        if failed:
            return None
    return None if "var(" in resolved else resolved


_CSS_NON_NAME_TOKENS = {
    "ease", "linear", "ease-in", "ease-out", "ease-in-out", "step-start", "step-end",
    "normal", "reverse", "alternate", "alternate-reverse", "forwards", "backwards",
    "both", "running", "paused",
}


def _shorthand_item(value: str) -> dict:
    duration = None
    delay = None
    loops: Union[float, str, None] = None
    name = "none"
    uncertain = False
    for token in _split_whitespace(value):
        lower = token.lower()
        time = _css_time(lower)
        if time is not None:
            if duration is None:
                duration = time
            elif delay is None:
                delay = time
            continue
        if lower == "infinite":
            loops = "infinite"
            continue
        if re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)", lower):
            loops = float(lower)
            continue
        if lower in _CSS_NON_NAME_TOKENS or re.match(r"^(?:cubic-bezier|steps|linear)\(", lower):
            continue
        if re.match(r"^(?:calc|min|max|clamp|var)\(", lower):
            uncertain = True
            continue
        name = token
    return {
        "name": name,
        "duration": 0 if duration is None else duration,
        "delay": 0 if delay is None else delay,
        "loops": 1 if loops is None else loops,
        "uncertain": uncertain,
    }


def _parsed_time_list(value: str, variables) -> List[Optional[float]]:
    result = []
    for item in _split_top_level(value, ","):
        resolved = _resolve_css_variables(item, variables)
        result.append(None if resolved is None else _css_time(resolved))
    return result


def _parsed_loop_list(value: str, variables) -> List[Union[float, str, None]]:
    result = []
    for item in _split_top_level(value, ","):
        resolved = _resolve_css_variables(item, variables)
        resolved = resolved.strip().lower() if resolved is not None else None
        if resolved == "infinite":
            result.append("infinite")
        elif resolved and re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)", resolved):
            result.append(float(resolved))
        else:
            result.append(None)
    return result


def _declarations(block: str) -> List[Tuple[str, str]]:
    result = []
    for declaration in _split_top_level(block, ";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        if prop.strip():
            result.append((prop.strip().lower(), value.strip()))
    return result


def _css_block_observations(path: str, block: str, variables) -> List[_Observation]:
    items = None
    names = None
    durations = None
    delays = None
    loops = None
    saw_property = False
    for prop, raw_value in _declarations(block):
        normalized = re.sub(r"^-webkit-", "", prop)
        if not normalized.startswith("animation"):
            continue
        saw_property = True
        if normalized == "animation":
            items = []
            for item in _split_top_level(raw_value, ","):
                resolved = _resolve_css_variables(item, variables)
                items.append(
                    {"name": "<dynamic>", "uncertain": True}
                    if resolved is None
                    else _shorthand_item(resolved)
                )
            names = [item.get("name") for item in items]
            durations = [item.get("duration") for item in items]
            delays = [item.get("delay") for item in items]
            loops = [item.get("loops") for item in items]
        elif normalized == "animation-name":
            names = [item.strip() for item in _split_top_level(raw_value, ",")]
        elif normalized == "animation-duration":
            durations = _parsed_time_list(raw_value, variables)
        elif normalized == "animation-delay":
            delays = _parsed_time_list(raw_value, variables)
        elif normalized == "animation-iteration-count":
            loops = _parsed_loop_list(raw_value, variables)

    if not saw_property:
        return []
    count = max(len(names or []), len(durations or []), len(delays or []), len(loops or []))
    observations = []
    for index in range(count):
        name = names[index % len(names)] if names else "<from CSS cascade>"
        if str(name).lower() == "none":
            continue
        duration = durations[index % len(durations)] if durations else None
        delay = delays[index % len(delays)] if delays else 0
        iterations = loops[index % len(loops)] if loops else 1
        inherited_uncertainty = bool(items and items[index % len(items)].get("uncertain"))
        if duration is None or delay is None or iterations is None:
            observations.append(_Observation(
                uncertain=True,
                evidence=f"{path}: CSS animation {name} has dynamic or cascade-dependent timing",
            ))
            continue
        if duration <= 0 or iterations == 0:
            continue
        if iterations == "infinite":
            observations.append(_Observation(
                infinite=True,
                uncertain=inherited_uncertainty,
                evidence=f"{path}: CSS animation {name} repeats indefinitely",
            ))
            continue
        total = max(0, delay + duration * iterations)
        plural = "" if iterations == 1 else "s"
        observations.append(_Observation(
            duration_ms=total,
            loops=iterations,
            uncertain=inherited_uncertainty or name == "<from CSS cascade>",
            evidence=f"{path}: CSS animation {name} ends at {_format_seconds(total)} ({iterations:g} iteration{plural})",
        ))
    return observations


def _css_observations(path: str, text: str, variables) -> List[_Observation]:
    clean = re.sub(r"/\*[\s\S]*?\*/", "", _css_source(path, text))
    blocks = [match.group(1) for match in re.finditer(r"\{([^{}]*)\}", clean)]
    result = []
    for block in blocks:
        result.extend(_css_block_observations(path, block, variables))
    return result


# ---- SVG / dynamic signals --------------------------------------------------

def _markup_attribute(tag: str, name: str) -> Optional[str]:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1", tag, re.I)
    return match.group(2) if match else None


def _svg_time(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    raw = value.strip().lower()
    css = _css_time(raw)
    if css is not None:
        return css
    unit = re.fullmatch(r"(-?(?:\d+(?:\.\d+)?|\.\d+))(min|h)", raw)
    if unit:
        return float(unit.group(1)) * (3_600_000 if unit.group(2) == "h" else 60_000)
    clock = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", raw)
    if not clock:
        return None
    return (
        int(clock.group(1) or 0) * 3600 + int(clock.group(2)) * 60 + float(clock.group(3))
    ) * 1000


def _svg_observations(path: str, text: str) -> List[_Observation]:
    result = []
    for match in re.finditer(r"<animate(?:Transform|Motion|Color)?\b[^>]*>", text, re.I):
        tag = match.group(0)
        duration = _svg_time(_markup_attribute(tag, "dur"))
        begin_raw = _markup_attribute(tag, "begin")
        begin = _svg_time(begin_raw.split(";")[0]) if begin_raw else 0
        repeat_raw = (_markup_attribute(tag, "repeatCount") or "").strip().lower()
        repeat_duration_raw = (_markup_attribute(tag, "repeatDur") or "").strip().lower()
        if repeat_raw == "indefinite" or repeat_duration_raw == "indefinite":
            result.append(_Observation(infinite=True, evidence=f"{path}: SVG animation repeats indefinitely"))
            continue
        repeat = float(repeat_raw) if re.fullmatch(r"\d+(?:\.\d+)?", repeat_raw) else 1
        repeat_duration = _svg_time(repeat_duration_raw)
        if duration is None or begin is None:
            result.append(_Observation(
                uncertain=True,
                evidence=f"{path}: SVG animation has event-based or dynamic timing",
            ))
            continue
        if duration <= 0 or repeat == 0:
            continue
        total = begin + (repeat_duration if repeat_duration is not None else duration * repeat)
        plural = "" if repeat == 1 else "s"
        result.append(_Observation(
            duration_ms=total,
            loops=repeat,
            evidence=f"{path}: SVG animation ends at {_format_seconds(total)} ({repeat:g} iteration{plural})",
        ))
    return result


def _dynamic_observations(path: str, text: str, has_viewst_model: bool) -> List[_Observation]:
    if has_viewst_model:
        return []
    result = []

    def add_unknown(label):
        result.append(_Observation(
            uncertain=True,
            evidence=f"{path}: {label} timing is runtime-defined",
        ))

    if re.search(r"\b(?:gsap|TweenMax|TweenLite)\s*\.\s*(?:to|from|fromTo|timeline)\s*\(|\bnew\s+Timeline(?:Max|Lite)\s*\(", text):
        if re.search(r"\brepeat\s*:\s*(?:-1|Infinity)\b", text):
            result.append(_Observation(infinite=True, evidence=f"{path}: GSAP declares an infinite repeat"))
        else:
            add_unknown("GSAP animation")
    if re.search(r"\bcreatejs\s*\.\s*(?:Tween|Ticker)\b", text):
        add_unknown("CreateJS animation")
    if re.search(r"\.animate\s*\(", text):
        if re.search(r"\biterations\s*:\s*Infinity\b", text):
            result.append(_Observation(infinite=True, evidence=f"{path}: Web Animations API repeats indefinitely"))
        else:
            add_unknown("Web Animations API")
    if re.search(r"\banime\s*\(", text):
        if re.search(r"\bloop\s*:\s*true\b", text):
            result.append(_Observation(infinite=True, evidence=f"{path}: anime.js declares an infinite loop"))
        else:
            add_unknown("anime.js animation")
    if re.search(r"\blottie\s*\.\s*loadAnimation\s*\(", text):
        if re.search(r"\bloop\s*:\s*true\b", text):
            result.append(_Observation(infinite=True, evidence=f"{path}: Lottie declares an infinite loop"))
        else:
            add_unknown("Lottie animation")
    if re.search(r"\brequestAnimationFrame\s*\(", text):
        add_unknown("requestAnimationFrame loop")
    if re.search(r"\bsetTimeout\s*\(", text):
        add_unknown("setTimeout schedule")
    if re.search(r"\bsetInterval\s*\(", text):
        add_unknown("setInterval loop")
    for match in re.finditer(r"<video\b[^>]*>", text, re.I):
        tag = match.group(0)
        if not re.search(r"\bautoplay\b", tag, re.I):
            continue
        if re.search(r"\bloop\b", tag, re.I):
            result.append(_Observation(infinite=True, evidence=f"{path}: autoplay video loops indefinitely"))
        else:
            add_unknown("autoplay video")
    return result


def analyze_animation(bundle: BannerBundle) -> AnimationAnalysis:
    texts = all_text(bundle)
    variables = _css_variables([
        re.sub(r"/\*[\s\S]*?\*/", "", _css_source(path, text))
        for path, text in texts
    ])
    observations: List[_Observation] = []
    for path, text in texts:
        viewst = _viewst_observations(path, text)
        observations.extend(viewst)
        observations.extend(_css_observations(path, text, variables))
        observations.extend(_svg_observations(path, text))
        observations.extend(
            _dynamic_observations(path, text, any(item.duration_ms is not None for item in viewst))
        )

    durations = [item.duration_ms for item in observations if item.duration_ms is not None]
    loops = [item.loops for item in observations if item.loops is not None]
    evidence = []
    for item in observations:
        if item.evidence not in evidence:
            evidence.append(item.evidence)
        if len(evidence) == MAX_EVIDENCE:
            break
    return AnimationAnalysis(
        detected=bool(observations),
        max_duration_ms=max(durations) if durations else None,
        max_loops=max(loops) if loops else None,
        infinite=any(item.infinite for item in observations),
        uncertain=any(item.uncertain for item in observations),
        evidence=evidence,
    )
