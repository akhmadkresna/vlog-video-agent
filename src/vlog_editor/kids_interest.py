"""Kids-interest (wow) scoring: payoff vs setup, not place hardcodes.

Axes:
  stimulus — animals / play / water / treats / ride / novelty (what is on offer)
  affect   — wonder / laugh / effort / desire (reaction; ASR or visual)
  phase    — payoff / setup / logistics
  clarity  — how readable the object is (close-up / detail)

Wow sits on top of kids_audience categories in audio_plan; discovery lexicons
here are split into stimulus vs affect so walk-up context does not beat reactions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vlog_editor.audio_plan import (
    KIDS_AUDIENCE_CATEGORIES,
    LAUGH_RE,
    PLAY_RE,
    kids_audience_categories,
)

# Stimulus-only subset (exclude overloaded "discovery" from audio_plan).
STIMULUS_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, pattern)
    for name, pattern in KIDS_AUDIENCE_CATEGORIES
    if name != "discovery"
)

NOVELTY_RE = re.compile(
    r"\b("
    r"besar\s*banget|gede\s*banget|huge|giant|close[- ]?up|closeup|"
    r"anomaly|misteri|tunnel|terowongan|cucut|shark|warna|colorful|"
    r"pertama\s*kali|first\s*time|baru\s*lihat"
    r")\b",
    re.IGNORECASE,
)

AFFECT_WONDER_RE = re.compile(
    r"\b("
    r"wow|wah|wih|surprise|kaget|curious|penasaran|amazing|keren|"
    r"lihat|look|point|tunjuk|lihat\s*ya|oh\s*ini|nah\s*ini|"
    r"besar\s*banget|gede\s*banget"
    r")\b",
    re.IGNORECASE,
)

AFFECT_EFFORT_RE = re.compile(
    r"\b("
    r"berani|berhasil|try|coba|pintar|careful|hati[- ]?hati|"
    r"pelan[- ]?pelan|master|naik|climb|meluncur"
    r")\b",
    re.IGNORECASE,
)

AFFECT_DESIRE_RE = re.compile(
    r"\b("
    r"mau|pengen|want|yummy|enak|donut|donat|ice\s*cream|es\s*krim|"
    r"beli|ambil"
    r")\b",
    re.IGNORECASE,
)

SETUP_RE = re.compile(
    r"\b("
    r"walk\s+toward|walking\s+toward|approach|approaching|"
    r"establishing|establish|heading\s+to|heading\s+toward|"
    r"context|toward\s+the|towards\s+the|enter(?:ing)?\s+the|"
    r"arrive|arrival|masuk|menuju|jalan\s+ke"
    r")\b",
    re.IGNORECASE,
)

LOGISTICS_RE = re.compile(
    r"\b("
    r"ticket|tiket|queue|antri|antrian|parking|parkir|"
    r"sunscreen|sunscreen|prepare|persiapan|arah|direction|"
    r"ayo\s+cepat|buruan|wait|tunggu|loket"
    r")\b",
    re.IGNORECASE,
)

CLOSEUP_RE = re.compile(
    r"\b(close[- ]?up|closeup|detail|macro|tight)\b",
    re.IGNORECASE,
)

WOW_SCORE_THRESHOLD = 0.45
FRAME_NARRATION_RADIUS_SEC = 4.0


def stimulus_labels(text: str) -> list[str]:
    blob = text or ""
    labels = [name for name, pattern in STIMULUS_CATEGORIES if pattern.search(blob)]
    if NOVELTY_RE.search(blob) and "novelty" not in labels:
        labels.append("novelty")
    return labels


def affect_labels(text: str) -> list[str]:
    blob = text or ""
    labels: list[str] = []
    if AFFECT_WONDER_RE.search(blob):
        labels.append("wonder")
    if LAUGH_RE.search(blob) or PLAY_RE.search(blob):
        labels.append("laugh")
    if AFFECT_EFFORT_RE.search(blob):
        labels.append("effort")
    if AFFECT_DESIRE_RE.search(blob):
        labels.append("desire")
    return labels


def infer_phase(text: str, *, visual_reason: str = "") -> str:
    blob = f"{text or ''} {visual_reason or ''}"
    if SETUP_RE.search(blob):
        return "setup"
    if LOGISTICS_RE.search(blob) and not stimulus_labels(blob):
        return "logistics"
    if LOGISTICS_RE.search(blob) and not affect_labels(blob):
        return "logistics"
    return "payoff"


def clarity_score(text: str, *, visual_reason: str = "", shot_type: str = "") -> float:
    blob = f"{text or ''} {visual_reason or ''}"
    shot = (shot_type or "").lower()
    score = 0.35
    if shot in {"close", "detail"} or CLOSEUP_RE.search(blob):
        score = 0.95
    elif shot in {"medium", "full"}:
        score = 0.55
    elif shot in {"wide"}:
        score = 0.3
    if CLOSEUP_RE.search(visual_reason or ""):
        score = max(score, 0.9)
    return score


def score_window(
    text: str,
    *,
    visual_reason: str = "",
    shot_type: str = "",
) -> dict[str, Any]:
    """Score one narration/visual window for kids-interest wow."""
    combined = f"{text or ''} {visual_reason or ''}".strip()
    stimulus = stimulus_labels(combined)
    # Affect prefers ASR/narration; fall back to visual reason.
    affect = affect_labels(text or "") or affect_labels(visual_reason or "")
    phase = infer_phase(text or "", visual_reason=visual_reason)
    clarity = clarity_score(text or "", visual_reason=visual_reason, shot_type=shot_type)

    reasons: list[str] = []
    for label in stimulus:
        reasons.append(f"stimulus:{label}")
    for label in affect:
        reasons.append(f"affect:{label}")
    if phase != "payoff":
        reasons.append(f"phase:{phase}")
    if clarity >= 0.85:
        reasons.append("clarity:close")

    stim_n = len(stimulus)
    aff_n = len(affect)
    base = 0.0
    if stim_n:
        base += 0.28 + 0.14 * min(2, stim_n - 1)
    if aff_n:
        base += 0.30 + 0.12 * min(2, aff_n - 1)
    # Stimulus alone with high clarity still counts (clear animal close-up).
    if stim_n and not aff_n and clarity >= 0.85:
        base += 0.18
    # Affect alone without stimulus is weaker (empty wow).
    if aff_n and not stim_n:
        base *= 0.55

    score = base * (0.55 + 0.45 * clarity)
    if phase == "setup":
        score *= 0.22
        # Pure establishing walk with kids mentioned stays low.
    elif phase == "logistics":
        score *= 0.18

    # Legacy category stacking still informs slightly when payoff.
    legacy = kids_audience_categories(combined)
    if phase == "payoff" and legacy:
        score = min(1.0, score + 0.04 * min(2, len(legacy)))

    return {
        "score": round(min(1.0, max(0.0, score)), 4),
        "stimulus": stimulus,
        "affect": affect,
        "phase": phase,
        "clarity": round(clarity, 3),
        "reasons": reasons,
    }


def _narration_in_span(source: dict[str, Any], start: float, end: float) -> str:
    segments = source.get("audio", {}).get("segments", []) or []
    parts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            seg_start = float(segment.get("start", 0))
            seg_end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if seg_end < start or seg_start > end:
            continue
        text = str(segment.get("text", "")).strip()
        if text:
            parts.append(text)
    if parts:
        return " ".join(parts)
    words = source.get("audio", {}).get("words", []) or []
    word_parts = [
        str(word.get("text", "")).strip()
        for word in words
        if isinstance(word, dict)
        and float(word.get("end", 0) or 0) >= start
        and float(word.get("start", 0) or 0) <= end
        and str(word.get("text", "")).strip()
    ]
    return " ".join(word_parts)


def _overlaps_avoided(
    start: float,
    end: float,
    avoided: list[tuple[float, float]],
) -> bool:
    for previous_start, previous_end in avoided:
        if min(end, previous_end) - max(start, previous_start) > 0.35:
            return True
    return False


def wow_moments(
    source: dict[str, Any],
    *,
    start: float = 0.0,
    end: float | None = None,
) -> list[dict[str, Any]]:
    """Build scored moments from ASR segments + recommended_ranges inside [start, end]."""
    source_duration = max(0.0, float(source.get("metadata", {}).get("duration", 0) or 0))
    span_end = source_duration if end is None else float(end)
    span_start = max(0.0, float(start))
    shot_type = str(source.get("visual", {}).get("shot_type", "") or "")

    raw: list[dict[str, Any]] = []

    for item in source.get("visual", {}).get("recommended_ranges", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            t0 = max(span_start, float(item.get("start", 0)))
            t1 = min(span_end, float(item.get("end", source_duration)))
        except (TypeError, ValueError):
            continue
        if t1 - t0 < 0.35:
            continue
        # Whole-clip / huge ranges are not beats — scoring their full ASR
        # would smear kids speech across the take and anchor excerpts at 0.
        range_span = t1 - t0
        if range_span > min(40.0, max(12.0, (span_end - span_start) * 0.45)):
            continue
        reason = str(item.get("reason", ""))
        narration = _narration_in_span(source, t0, t1)
        scored = score_window(narration, visual_reason=reason, shot_type=shot_type)
        raw.append(
            {
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "score": scored["score"],
                "phase": scored["phase"],
                "stimulus": scored["stimulus"],
                "affect": scored["affect"],
                "narration": narration,
                "reasons": list(scored["reasons"]) + ([f"range:{reason[:80]}"] if reason else []),
            }
        )

    for segment in source.get("audio", {}).get("segments", []) or []:
        if not isinstance(segment, dict):
            continue
        try:
            t0 = max(span_start, float(segment.get("start", 0)))
            t1 = min(span_end, float(segment.get("end", 0)))
        except (TypeError, ValueError):
            continue
        if t1 - t0 < 0.35:
            continue
        narration = str(segment.get("text", "")).strip()
        if not narration:
            continue
        scored = score_window(narration, shot_type=shot_type)
        if scored["score"] < 0.2 and scored["phase"] != "setup":
            # Keep weak segments out of the moment list unless they are setup
            # (setup is useful for demotion visibility).
            if not scored["stimulus"] and not scored["affect"]:
                continue
        raw.append(
            {
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "score": scored["score"],
                "phase": scored["phase"],
                "stimulus": scored["stimulus"],
                "affect": scored["affect"],
                "narration": narration,
                "reasons": list(scored["reasons"]) + ["asr:segment"],
            }
        )

    if not raw:
        return []

    # Cluster nearby moments into ~8–20s windows, keeping best score/reasons.
    raw.sort(key=lambda item: (float(item["t0"]), float(item["t1"])))
    clusters: list[dict[str, Any]] = []
    for item in raw:
        if not clusters:
            clusters.append(dict(item))
            continue
        prev = clusters[-1]
        gap = float(item["t0"]) - float(prev["t1"])
        span = float(item["t1"]) - float(prev["t0"])
        if gap <= 3.0 and span <= 22.0:
            prev["t1"] = max(float(prev["t1"]), float(item["t1"]))
            if float(item["score"]) > float(prev["score"]):
                prev["score"] = item["score"]
                prev["phase"] = item["phase"]
                prev["stimulus"] = item["stimulus"]
                prev["affect"] = item["affect"]
            else:
                # Merge labels.
                prev["stimulus"] = sorted(set(prev.get("stimulus") or []) | set(item.get("stimulus") or []))
                prev["affect"] = sorted(set(prev.get("affect") or []) | set(item.get("affect") or []))
            narr = " ".join(
                part
                for part in (str(prev.get("narration") or ""), str(item.get("narration") or ""))
                if part
            ).strip()
            prev["narration"] = narr
            reasons = list(dict.fromkeys([*(prev.get("reasons") or []), *(item.get("reasons") or [])]))
            prev["reasons"] = reasons[:8]
            prev["t0"] = round(float(prev["t0"]), 3)
            prev["t1"] = round(float(prev["t1"]), 3)
        else:
            clusters.append(dict(item))

    clusters.sort(key=lambda item: (-float(item["score"]), float(item["t0"])))
    return clusters


def best_wow_span(
    source: dict[str, Any],
    max_duration: float,
    *,
    avoid: list[tuple[float, float]] | None = None,
    min_score: float = WOW_SCORE_THRESHOLD,
) -> tuple[float, float, dict[str, Any]] | None:
    """Return (start, end, moment) for the best payoff span that fits max_duration."""
    source_duration = max(0.0, float(source.get("metadata", {}).get("duration", 0) or 0))
    max_duration = min(max(0.0, max_duration), source_duration)
    if max_duration < 0.35:
        return None
    avoided = avoid or []
    moments = wow_moments(source, start=0.0, end=source_duration)
    candidates: list[tuple[float, float, float, dict[str, Any]]] = []
    for moment in moments:
        if float(moment.get("score", 0) or 0) < min_score:
            continue
        if str(moment.get("phase") or "") == "setup":
            continue
        t0 = float(moment["t0"])
        t1 = float(moment["t1"])
        start, end = _grow_forward(
            t0=t0,
            t1=t1,
            max_duration=max_duration,
            source_duration=source_duration,
        )
        if end - start < 0.35 or _overlaps_avoided(start, end, avoided):
            continue
        affect_n = len(moment.get("affect") or [])
        # Prefer reaction+stimulus payoffs over silent close-ups when scores are close.
        candidates.append((float(moment["score"]) + 0.05 * affect_n, start, end, moment))

    if not candidates:
        return None
    _, start, end, moment = max(candidates, key=lambda row: (row[0], row[2] - row[1]))
    return round(start, 3), round(end, 3), moment


def _grow_forward(
    *,
    t0: float,
    t1: float,
    max_duration: float,
    source_duration: float,
) -> tuple[float, float]:
    """Anchor at payoff start; grow forward, with only a tiny pre-roll."""
    pre_roll = min(0.4, max(0.0, t0))
    start = max(0.0, t0 - pre_roll)
    end = min(source_duration, max(t1, start + max_duration))
    if end - start > max_duration + 1e-6:
        end = min(source_duration, start + max_duration)
    if end - start < 0.35:
        return t0, min(source_duration, t0 + max_duration)
    return start, end


def source_wow_summary(source: dict[str, Any], *, start: float, end: float) -> dict[str, Any]:
    """Aggregate wow metadata for a planned excerpt."""
    moments = wow_moments(source, start=start, end=end)
    if not moments:
        narration = _narration_in_span(source, start, end)
        scored = score_window(
            narration,
            visual_reason=str(source.get("visual", {}).get("summary", "") or ""),
            shot_type=str(source.get("visual", {}).get("shot_type", "") or ""),
        )
        return {
            "score": scored["score"],
            "phase": scored["phase"],
            "stimulus": scored["stimulus"],
            "affect": scored["affect"],
            "reasons": scored["reasons"],
        }
    best = max(moments, key=lambda item: float(item.get("score", 0) or 0))
    return {
        "score": float(best["score"]),
        "phase": str(best.get("phase") or "payoff"),
        "stimulus": list(best.get("stimulus") or []),
        "affect": list(best.get("affect") or []),
        "reasons": list(best.get("reasons") or [])[:8],
    }


def source_has_strong_wow(source: dict[str, Any], *, min_score: float = WOW_SCORE_THRESHOLD) -> bool:
    moments = wow_moments(source)
    return any(
        float(m.get("score", 0) or 0) >= min_score and str(m.get("phase") or "") != "setup"
        for m in moments
    )


def parse_frame_timestamp(path: str | Path) -> float | None:
    """Parse timestamp from frame_001_12.345.jpg style names."""
    name = Path(path).name
    match = re.search(r"frame_\d+_(\d+(?:\.\d+)?)\.jpe?g$", name, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def narration_near_time(
    source: dict[str, Any],
    timestamp: float,
    *,
    radius: float = FRAME_NARRATION_RADIUS_SEC,
) -> str:
    return _narration_in_span(source, timestamp - radius, timestamp + radius)


def frame_wow_review(
    source: dict[str, Any],
    timestamp: float,
    *,
    radius: float = FRAME_NARRATION_RADIUS_SEC,
) -> dict[str, Any]:
    narration = narration_near_time(source, timestamp, radius=radius)
    # Prefer nearest recommended range reason if any overlaps the frame.
    visual_reason = ""
    for item in source.get("visual", {}).get("recommended_ranges", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            t0 = float(item.get("start", 0))
            t1 = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        if t0 - 0.5 <= timestamp <= t1 + 0.5:
            visual_reason = str(item.get("reason", ""))
            break
    scored = score_window(
        narration,
        visual_reason=visual_reason,
        shot_type=str(source.get("visual", {}).get("shot_type", "") or ""),
    )
    scored["narration"] = narration
    scored["t"] = round(timestamp, 3)
    return scored
