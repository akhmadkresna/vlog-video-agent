from __future__ import annotations

import hashlib
import html
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


def _clip_cards(episode: Episode, plan: dict[str, Any]) -> tuple[str, set[Path]]:
    cards: list[str] = []
    used_thumbnails: set[Path] = set()
    dashboard = episode.work / "dashboard"
    timeline_cursor = 0.0
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
            thumbnail(source, thumb_path, (start + end) / 2)
            used_thumbnails.add(thumb_path)
            relative = thumb_path.relative_to(dashboard).as_posix()
            duration = end - start
            timeline_start = timeline_cursor
            timeline_end = timeline_cursor + duration
            timeline_cursor = timeline_end
            subtitle = html.escape(str(clip.get("subtitle", "")))
            cards.append(
                '<article class="card">'
                f'<img src="{relative}" alt="">'
                f'<div class="body"><strong>{html.escape(str(clip["file"]))}</strong>'
                f'<div class="time">Edit {format_clock(timeline_start)} → '
                f'{format_clock(timeline_end)} · {duration:.2f}s</div>'
                f'<div class="source">Source {start:.2f}s → {end:.2f}s</div>'
                f'<p>{html.escape(str(clip.get("note", "")))}</p>'
                f'<p class="speech">{subtitle}</p></div></article>'
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
grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}} .card{{background:#171d27;
border:1px solid #293242;border-radius:10px;overflow:hidden}} img{{width:100%;aspect-ratio:16/9;
object-fit:cover;background:#080a0d}} .body{{padding:14px}} .time{{font:13px ui-monospace;
color:#7dd3fc;margin-top:6px}} .source{{font:12px ui-monospace;color:#9ca9ba;margin-top:4px}}
.speech{{color:#e8bd72}} code{{color:#7dd3fc}}
.cues{{margin:8px 0 0;padding-left:18px;color:#c6d0db}} .cues li{{margin:4px 0}}
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
