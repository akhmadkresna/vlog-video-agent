from __future__ import annotations

import hashlib
import html
import shutil
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vlog_editor.kids_interest import (
    frame_wow_review,
    parse_frame_timestamp,
    source_wow_summary,
)
from vlog_editor.media import thumbnail
from vlog_editor.project import Episode, read_json, write_json


def plan_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approve_plan(episode: Episode) -> dict[str, Any]:
    if not episode.plan_path.is_file():
        raise FileNotFoundError("Missing edit plan. Run `ve plan` first.")
    approval = {
        "approved": True,
        "plan_sha256": plan_digest(episode.plan_path),
        "approved_at": datetime.now(UTC).isoformat(),
    }
    write_json(episode.approval_path, approval)
    return approval


def require_approval(episode: Episode) -> None:
    if not episode.approval_path.is_file():
        raise PermissionError("Plan is not approved. Review it and run `ve approve`.")
    approval = read_json(episode.approval_path)
    if not approval.get("approved"):
        raise PermissionError("Plan approval is false.")
    if approval.get("plan_sha256") != plan_digest(episode.plan_path):
        raise PermissionError("Plan changed after approval. Review and approve it again.")


def _thumbnail_path(
    dashboard: Path,
    source: Path,
    *,
    start: float,
    end: float,
) -> Path:
    stat = source.stat()
    identity = (
        f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|"
        f"{start:.3f}|{end:.3f}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return dashboard / "assets" / f"thumb_{digest}.jpg"


def format_clock(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _analysis_by_filename(analysis: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not analysis:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for clip in analysis.get("clips", []) or []:
        if not isinstance(clip, dict):
            continue
        filename = str(clip.get("metadata", {}).get("filename", "") or "")
        if filename:
            out[filename] = clip
    return out


def _stage_frame_asset(
    dashboard: Path,
    frame_path: Path,
    used: set[Path],
) -> str | None:
    if not frame_path.is_file():
        return None
    digest = hashlib.sha256(str(frame_path.resolve()).encode("utf-8")).hexdigest()[:16]
    dest = dashboard / "assets" / f"frame_{digest}{frame_path.suffix.lower() or '.jpg'}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or dest.stat().st_size == 0:
        try:
            shutil.copy2(frame_path, dest)
        except OSError:
            return None
    used.add(dest)
    return dest.relative_to(dashboard).as_posix()


def _wow_badge_html(wow: dict[str, Any] | None) -> str:
    if not isinstance(wow, dict) or not wow:
        return ""
    try:
        score = float(wow.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0
    phase = html.escape(str(wow.get("phase") or "payoff"))
    stimulus = ", ".join(html.escape(str(x)) for x in (wow.get("stimulus") or [])[:4])
    affect = ", ".join(html.escape(str(x)) for x in (wow.get("affect") or [])[:4])
    reasons = ", ".join(html.escape(str(x)) for x in (wow.get("reasons") or [])[:4])
    chips = []
    if stimulus:
        chips.append(f'<span class="chip">{stimulus}</span>')
    if affect:
        chips.append(f'<span class="chip affect">{affect}</span>')
    return (
        f'<div class="wow-badge phase-{phase}" data-wow-score="{score:.2f}">'
        f'<strong>Wow {score:.2f}</strong> · {phase}'
        f'<div class="chips">{"".join(chips)}</div>'
        f'<div class="wow-reasons">{reasons}</div></div>'
    )


def _frame_strip_html(
    episode: Episode,
    dashboard: Path,
    source_clip: dict[str, Any] | None,
    *,
    start: float,
    end: float,
    used_assets: set[Path],
) -> str:
    if not source_clip:
        return ""
    frames = source_clip.get("frames") or []
    cells: list[str] = []
    for frame_rel in frames:
        frame_path = episode.root / str(frame_rel)
        timestamp = parse_frame_timestamp(frame_path)
        if timestamp is None:
            continue
        if timestamp < start - 0.25 or timestamp > end + 0.25:
            continue
        relative = _stage_frame_asset(dashboard, frame_path, used_assets)
        if relative is None:
            continue
        review = frame_wow_review(source_clip, timestamp)
        score = float(review.get("score", 0) or 0)
        phase = html.escape(str(review.get("phase") or "payoff"))
        narration = html.escape(str(review.get("narration") or "").strip() or "(no speech)")
        stim = ", ".join(html.escape(str(x)) for x in (review.get("stimulus") or [])[:3])
        aff = ", ".join(html.escape(str(x)) for x in (review.get("affect") or [])[:3])
        reasons = ", ".join(html.escape(str(x)) for x in (review.get("reasons") or [])[:3])
        strength = "hot" if score >= 0.55 else "warm" if score >= 0.35 else "cool"
        cells.append(
            f'<div class="frame-cell {strength} phase-{phase}" data-wow-score="{score:.2f}">'
            f'<img src="{relative}" alt="">'
            f'<div class="frame-meta">'
            f'<div class="frame-time">{timestamp:.1f}s · wow {score:.2f} · {phase}</div>'
            f'<div class="frame-narration">{narration}</div>'
            f'<div class="frame-wow">{stim}{" · " if stim and aff else ""}{aff}</div>'
            f'<div class="frame-reasons">{reasons}</div>'
            f"</div></div>"
        )
    if not cells:
        return ""
    return '<div class="frame-strip" data-frame-review="1">' + "".join(cells) + "</div>"


def render_clip_card_html(
    episode: Episode,
    clip: dict[str, Any],
    *,
    timeline_start: float,
    timeline_end: float,
    duration: float,
    thumb_relative: str,
    source_clip: dict[str, Any] | None,
    dashboard: Path,
    used_assets: set[Path],
) -> str:
    """Build one storyboard card (used by dashboard + tests)."""
    start = float(clip["start"])
    end = float(clip["end"])
    subtitle = html.escape(str(clip.get("subtitle", "")))
    wow = clip.get("wow") if isinstance(clip.get("wow"), dict) else None
    if wow is None and source_clip is not None:
        wow = source_wow_summary(source_clip, start=start, end=end)
    badge = _wow_badge_html(wow)
    strip = _frame_strip_html(
        episode,
        dashboard,
        source_clip,
        start=start,
        end=end,
        used_assets=used_assets,
    )
    return (
        '<article class="card">'
        f'<img src="{thumb_relative}" alt="">'
        f'<div class="body"><strong>{html.escape(str(clip["file"]))}</strong>'
        f'<div class="time">Edit {format_clock(timeline_start)} → '
        f'{format_clock(timeline_end)} · {duration:.2f}s</div>'
        f'<div class="source">Source {start:.2f}s → {end:.2f}s</div>'
        f"{badge}"
        f'<p>{html.escape(str(clip.get("note", "")))}</p>'
        f'<p class="speech">{subtitle}</p>'
        f"{strip}</div></article>"
    )


def _clip_cards(episode: Episode, plan: dict[str, Any]) -> tuple[str, set[Path]]:
    cards: list[str] = []
    used_thumbnails: set[Path] = set()
    dashboard = episode.work / "dashboard"
    timeline_cursor = 0.0
    analysis: dict[str, Any] | None = None
    if episode.analysis_path.is_file():
        try:
            loaded = read_json(episode.analysis_path)
            if isinstance(loaded, dict):
                analysis = loaded
        except (OSError, ValueError, TypeError):
            analysis = None
    by_file = _analysis_by_filename(analysis)

    for section_index, section in enumerate(plan.get("structure", []), start=1):
        cards.append(
            f'<section><h2>{section_index}. {html.escape(str(section.get("section", "")))}</h2>'
            f'<p class="description">{html.escape(str(section.get("description", "")))}</p>'
            '<div class="grid">'
        )
        for clip in section.get("clips", []):
            source = episode.footage / str(clip["file"])
            start = float(clip["start"])
            end = float(clip["end"])
            thumb_path = _thumbnail_path(dashboard, source, start=start, end=end)
            if source.is_file():
                thumbnail(source, thumb_path, (start + end) / 2)
            used_thumbnails.add(thumb_path)
            relative = thumb_path.relative_to(dashboard).as_posix()
            duration = end - start
            timeline_start = timeline_cursor
            timeline_end = timeline_cursor + duration
            timeline_cursor = timeline_end
            cards.append(
                render_clip_card_html(
                    episode,
                    clip,
                    timeline_start=timeline_start,
                    timeline_end=timeline_end,
                    duration=duration,
                    thumb_relative=relative,
                    source_clip=by_file.get(str(clip["file"])),
                    dashboard=dashboard,
                    used_assets=used_thumbnails,
                )
            )
        cards.append("</div></section>")
    return "".join(cards), used_thumbnails


def generate_dashboard(episode: Episode, *, open_browser: bool = True) -> Path:
    if not episode.plan_path.is_file():
        raise FileNotFoundError("Missing edit plan. Run `ve plan` first.")
    plan = read_json(episode.plan_path)
    dashboard = episode.work / "dashboard"
    dashboard.mkdir(parents=True, exist_ok=True)
    cards, used_thumbnails = _clip_cards(episode, plan)
    assets = dashboard / "assets"
    if assets.is_dir():
        for old_thumbnail in assets.glob("*.jpg"):
            if old_thumbnail not in used_thumbnails:
                old_thumbnail.unlink()

    bgm = plan.get("bgm") if isinstance(plan.get("bgm"), dict) else None
    bgm_bed_items: list[str] = []
    if bgm and bgm.get("file"):
        mode = str(bgm.get("mode", "full"))
        bgm_text = (
            f"{bgm.get('file')} · mode {mode} · license {bgm.get('license', 'unknown')} · "
            f"vol {float(bgm.get('volume', 0.1)):.2f}"
        )
        if bgm.get("attribution"):
            bgm_text += f" · credit {bgm['attribution']}"
        for segment in bgm.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            start = float(segment.get("start_sec", 0))
            end = float(segment.get("end_sec", 0))
            track = Path(str(segment.get("file") or bgm.get("file") or "")).name
            bgm_bed_items.append(
                "<li>"
                f"<code>{html.escape(format_clock(start))}"
                f"-{html.escape(format_clock(end))}</code> · "
                f"vol {float(segment.get('volume', 0.12)):.2f} · "
                f"{html.escape(track)} · "
                f"{html.escape(str(segment.get('reason', '')))}"
                "</li>"
            )
    else:
        bgm_text = str(plan.get("bgm_suggestion", "") or "none")
    beds_html = (
        "<ul class='cues'>" + "".join(bgm_bed_items) + "</ul>"
        if bgm_bed_items
        else (
            "<p class='description'>Whole-edit BGM (no beds), or music disabled.</p>"
            if bgm and bgm.get("file")
            else ""
        )
    )

    cue_items = []
    for cue in plan.get("audio_cues", []) or []:
        if not isinstance(cue, dict):
            continue
        cue_items.append(
            "<li>"
            f"<code>{html.escape(format_clock(float(cue.get('at_sec', 0))))}</code> · "
            f"{html.escape(str(cue.get('type', '')))} · "
            f"{html.escape(str(cue.get('license', '')))} · "
            f"{html.escape(str(cue.get('reason', '')))}"
            "</li>"
        )
    cues_html = (
        "<ul class='cues'>" + "".join(cue_items) + "</ul>"
        if cue_items
        else "<p class='description'>No SFX cues (empty license-free pack or audio disabled).</p>"
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(plan.get("title", "Vlog Plan")))}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#0e1116;color:#e8edf4;
font:15px/1.5 system-ui,sans-serif}} header,main{{max-width:1400px;margin:auto;padding:28px}}
header{{background:#171d27;border-bottom:1px solid #293242}} h1{{margin:0 0 8px}}
.meta,.description{{color:#9ca9ba}} section{{margin:36px 0}} .grid{{display:grid;
grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}} .card{{background:#171d27;
border:1px solid #293242;border-radius:10px;overflow:hidden}} .card>img{{width:100%;aspect-ratio:16/9;
object-fit:cover;background:#080a0d}} .body{{padding:14px}} .time{{font:13px ui-monospace;
color:#7dd3fc;margin-top:6px}} .source{{font:12px ui-monospace;color:#9ca9ba;margin-top:4px}}
.speech{{color:#e8bd72}} code{{color:#7dd3fc}}
.cues{{margin:8px 0 0;padding-left:18px;color:#c6d0db}} .cues li{{margin:4px 0}}
.wow-badge{{margin-top:10px;padding:8px 10px;border-radius:8px;background:#101826;
border:1px solid #334155;font-size:13px}} .wow-badge.phase-setup{{opacity:.72}}
.wow-badge.phase-payoff{{border-color:#3d6b4f}} .chips{{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}}
.chip{{display:inline-block;padding:2px 8px;border-radius:999px;background:#243044;color:#c7d2fe;font-size:12px}}
.chip.affect{{background:#3b2f1d;color:#fde68a}} .wow-reasons{{margin-top:4px;color:#9ca9ba;font-size:12px}}
.frame-strip{{display:flex;gap:10px;overflow-x:auto;margin-top:12px;padding-bottom:4px}}
.frame-cell{{flex:0 0 168px;background:#0b1018;border:1px solid #2a3548;border-radius:8px;overflow:hidden}}
.frame-cell img{{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#080a0d}}
.frame-cell.hot{{border-color:#4ade80;opacity:1}} .frame-cell.warm{{border-color:#fbbf24;opacity:.95}}
.frame-cell.cool{{opacity:.78}} .frame-cell.phase-setup{{opacity:.55}}
.frame-meta{{padding:8px;font-size:11px;line-height:1.35}} .frame-time{{color:#7dd3fc;font-family:ui-monospace,monospace}}
.frame-narration{{color:#e8bd72;margin-top:4px;max-height:4.2em;overflow:hidden}}
.frame-wow{{color:#c7d2fe;margin-top:4px}} .frame-reasons{{color:#8b98a8;margin-top:2px}}
</style></head>
<body><header><h1>{html.escape(str(plan.get("title", "Vlog Plan")))}</h1>
<div class="meta">{len(plan.get("structure", []))} sections ·
{float(plan.get("duration_sec", 0)):.1f}s ·
Day <code>{html.escape(str(plan.get("planning_day", "auto")))}</code> ·
Plan <code>{plan_digest(episode.plan_path)[:12]}</code></div>
<p>{html.escape(str(plan.get("editing_notes", "")))}</p>
<p><strong>BGM:</strong> {html.escape(bgm_text)}</p>
{beds_html}
<p><strong>SFX cues:</strong></p>
{cues_html}
<p>After reviewing, run <code>ve approve {html.escape(str(episode.root))}</code>.</p>
</header><main>{cards}</main></body></html>"""
    output = dashboard / "index.html"
    output.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(output.as_uri())
    print(f"Wrote {output}")
    return output
