from __future__ import annotations

import json
from typing import Any

from vlog_editor.project import Episode, read_json, write_json
from vlog_editor.validation import validate_and_fix_plan
from vlog_editor.vision import OllamaClient, parse_json_response


def _compact_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for clip in analysis.get("clips", []):
        metadata = clip["metadata"]
        audio = clip.get("audio", {})
        visual = clip.get("visual", {})
        compact.append(
            {
                "file": metadata["filename"],
                "duration": metadata["duration"],
                "orientation": metadata["orientation"],
                "summary": visual.get("summary", ""),
                "shot_type": visual.get("shot_type", ""),
                "mood": visual.get("mood", ""),
                "quality": visual.get("quality", 0),
                "motion": visual.get("motion", 0),
                "story_value": visual.get("story_value", 0),
                "issues": visual.get("issues", []),
                "recommended_ranges": visual.get("recommended_ranges", []),
                "transcript": str(audio.get("text", ""))[:400],
                "speech_segments": audio.get("segments", [])[:12],
            }
        )
    return compact


def _planning_prompt(episode: Episode, compact: list[dict[str, Any]]) -> str:
    target = float(episode.config["target_duration_sec"])
    return f"""
Act as a decisive travel-vlog editor. Build a coherent edit from the clip analysis below.
Title/theme: {episode.config['title']}
Style: {episode.config['style']}
Target duration: {target:.0f} seconds

Rules:
- Use a hook, development, highlight, and emotional close; 4-10 sections total.
- Prefer landscape, high quality, motion and story value. Skip poor/redundant footage.
- Pure visual shots usually last 2-6 seconds. Preserve complete useful speech.
- Ranges must remain within source duration and may use a source more than once only for distinct ranges.
- Aim within 25% of target duration. Do not fabricate filenames or transcript.

Return only JSON:
{{
  "title": "title",
  "structure": [
    {{
      "section": "Section name — subtitle",
      "description": "purpose",
      "clips": [
        {{"file": "exact filename", "start": 0.0, "end": 4.0,
          "note": "visual purpose", "subtitle": "exact retained speech or empty"}}
      ]
    }}
  ],
  "bgm_suggestion": "genre, mood, instruments, tempo and bpm",
  "editing_notes": "short rationale"
}}

Clip analysis:
{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}
""".strip()


def create_plan(episode: Episode) -> dict[str, Any]:
    if not episode.analysis_path.is_file():
        raise FileNotFoundError("Missing clip analysis. Run `ve analyze` first.")
    analysis = read_json(episode.analysis_path)
    compact = _compact_analysis(analysis)
    client = OllamaClient(dict(episode.config["vision"]))
    try:
        content, _ = client.chat(
            _planning_prompt(episode, compact),
            timeout=900,
            max_tokens=4096,
            context_tokens=32768,
        )
        plan = parse_json_response(content)
        fixed, errors = validate_and_fix_plan(
            plan,
            analysis,
            target_duration=float(episode.config["target_duration_sec"]),
        )
        if errors:
            repair_prompt = f"""
Repair this edit plan. Return only the complete corrected JSON plan.
Validation errors:
{json.dumps(errors, ensure_ascii=False)}

Valid clip analysis:
{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}

Broken plan:
{json.dumps(fixed, ensure_ascii=False, separators=(',', ':'))}
""".strip()
            repaired_content, _ = client.chat(
                repair_prompt,
                timeout=900,
                max_tokens=4096,
                context_tokens=32768,
            )
            repaired = parse_json_response(repaired_content)
            fixed, errors = validate_and_fix_plan(
                repaired,
                analysis,
                target_duration=float(episode.config["target_duration_sec"]),
            )
        if errors:
            raise ValueError("Local planner produced an invalid plan:\n- " + "\n- ".join(errors))
        write_json(episode.plan_path, fixed)
        if episode.approval_path.exists():
            episode.approval_path.unlink()
        print(f"Wrote {episode.plan_path} ({fixed['duration_sec']:.1f}s)")
        return fixed
    finally:
        client.unload()
