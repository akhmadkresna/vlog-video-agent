---
name: vlog-editor
description: Creates locally analyzed travel and B-roll vlogs with faster-whisper, Qwen3-VL through Ollama, FFmpeg, a review dashboard, and approval-gated rendering. Use when the user asks to analyze footage, build a vlog, select clips, review an edit plan, or render a vlog episode.
---

# Local Vlog Editor

Use the `ve` CLI. Do not recreate its media logic in ad-hoc scripts.

## Workflow

1. Run `ve doctor`.
2. For a new episode, run `ve new <episode-path>`.
3. Ask the user to place source videos in `footage/` and edit `project.yaml`.
4. Run `ve analyze <episode>`. Preserve `work/cache/`; do not reanalyze unchanged media.
5. Run `ve plan <episode>`.
6. Run `ve preview <episode>` and wait for explicit user review.
7. If the user requests plan changes, edit `work/edit_plan.json`, rerun `ve preview`, and wait again.
8. Only after explicit approval, run `ve approve <episode>`, then `ve render <episode>`.
9. Report `output/final.mp4` and `output/verification.json`.

`ve run <episode>` performs steps 4-6 and intentionally stops before approval.

## Hard rules

- Source files under `footage/` are read-only.
- All AI inference stays local through Ollama at `127.0.0.1`.
- Never bypass the plan-hash approval gate.
- Never cut through a spoken word; retain validator corrections.
- Do not run faster-whisper and Qwen concurrently on the 8 GB GPU.
- Use user-supplied licensed BGM only. Do not generate or download music.
- Keep cached analysis unless the footage, model, or settings changed.
