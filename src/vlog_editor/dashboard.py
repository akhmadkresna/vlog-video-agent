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


def _clip_cards(episode: Episode, plan: dict[str, Any]) -> str:
    cards: list[str] = []
    index = 0
    dashboard = episode.work / "dashboard"
    for section_index, section in enumerate(plan.get("structure", []), start=1):
        cards.append(
            f'<section><h2>{section_index}. {html.escape(str(section.get("section", "")))}</h2>'
            f'<p class="description">{html.escape(str(section.get("description", "")))}</p>'
            '<div class="grid">'
        )
        for clip in section.get("clips", []):
            index += 1
            source = episode.footage / str(clip["file"])
            thumb_path = dashboard / "assets" / f"clip_{index:04d}.jpg"
            thumbnail(source, thumb_path, (float(clip["start"]) + float(clip["end"])) / 2)
            relative = thumb_path.relative_to(dashboard).as_posix()
            duration = float(clip["end"]) - float(clip["start"])
            subtitle = html.escape(str(clip.get("subtitle", "")))
            cards.append(
                '<article class="card">'
                f'<img src="{relative}" alt="">'
                f'<div class="body"><strong>{html.escape(str(clip["file"]))}</strong>'
                f'<div class="time">{float(clip["start"]):.2f}s → '
                f'{float(clip["end"]):.2f}s · {duration:.2f}s</div>'
                f'<p>{html.escape(str(clip.get("note", "")))}</p>'
                f'<p class="speech">{subtitle}</p></div></article>'
            )
        cards.append("</div></section>")
    return "".join(cards)


def generate_dashboard(episode: Episode, *, open_browser: bool = True) -> Path:
    if not episode.plan_path.is_file():
        raise FileNotFoundError("Missing edit plan. Run `ve plan` first.")
    plan = read_json(episode.plan_path)
    dashboard = episode.work / "dashboard"
    dashboard.mkdir(parents=True, exist_ok=True)
    cards = _clip_cards(episode, plan)
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
color:#7dd3fc;margin-top:6px}} .speech{{color:#e8bd72}} code{{color:#7dd3fc}}
</style></head>
<body><header><h1>{html.escape(str(plan.get("title", "Vlog Plan")))}</h1>
<div class="meta">{len(plan.get("structure", []))} sections ·
{float(plan.get("duration_sec", 0)):.1f}s · Plan <code>{plan_digest(episode.plan_path)[:12]}</code></div>
<p>{html.escape(str(plan.get("editing_notes", "")))}</p>
<p><strong>BGM:</strong> {html.escape(str(plan.get("bgm_suggestion", "")))}</p>
<p>After reviewing, run <code>ve approve {html.escape(str(episode.root))}</code>.</p>
</header><main>{cards}</main></body></html>"""
    output = dashboard / "index.html"
    output.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(output.as_uri())
    print(f"Wrote {output}")
    return output
