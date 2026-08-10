---
name: vlog-editor
description: Creates locally analyzed travel and B-roll vlogs with faster-whisper, Qwen3-VL through Ollama, FFmpeg, a review dashboard, and approval-gated rendering. Use when the user asks to analyze footage, build a vlog, select clips, review an edit plan, or render a vlog episode.
---

# Local Vlog Editor

Use the `ve` CLI. Do not recreate its media logic in ad-hoc scripts.

## Workflow

1. Run `ve doctor`.
2. For a new episode, run `ve new <episode-path>`.
3. Ask the user to place source videos in `footage/`, optionally customize the seeded license-free `audio/` pack (or add BGM), and edit `project.yaml`.
4. Run `ve analyze <episode>`. Preserve `work/cache/`; do not reanalyze unchanged media.
5. Run `ve plan <episode>`.
6. Run `ve preview <episode>` and wait for explicit user review of clips, SFX cues, and BGM.
7. If the user requests plan changes, edit `work/edit_plan.json`, rerun `ve preview`, and wait again.
8. Only after explicit approval, run `ve approve <episode>`, then `ve render <episode>`.
9. Report `output/final.mp4` and `output/verification.json`.

`ve run <episode>` performs steps 4-6 and intentionally stops before approval.

## Hard rules

- Source files under `footage/` are read-only.
- All AI inference stays local through Ollama at `127.0.0.1`.
- Never bypass the plan-hash approval gate.
- Never cut through a spoken word; retain validator corrections.
- Keep one primary capture day. Default `story_arc: scene_energy` uses contiguous
  same-setting scenes in time order, ranking best→better kids energy inside each scene
  (morning car ≠ night car). Alternatives via episode `project.yaml`: `kids_energy` or
  `chronological`.
- Framework defaults live in code/`DEFAULT_CONFIG`. Episode-specific taste from review
  feedback (duration, language, captions, arc, SFX density, forced includes/excludes)
  belongs in that episode's `project.yaml` or `work/edit_plan.json` — do not hardcode
  place names or one family's day into the planner.
- Prefer original footage over duplicate `Copy` files.
- Do not run faster-whisper and Qwen concurrently on the 8 GB GPU.
- Use local audio packs only (`cc0`, `public_domain`, `youtube_audio_library`, `pixabay`, `original`, `user_provided`).
- Prefer the bundled default pack (Leika-style Indo kids punctuation + Pixabay kids BGM + optional `sfx/meme/` roles). Do not generate synthetic SFX/BGM, scrape YouTube/CapCut audio, or auto-download at runtime. Meme files are opt-in local drops tagged `user_provided` with a `role` — never auto-ripped.
- Review SFX cues and BGM in the dashboard before approval.
- Keep cached analysis unless the footage, model, or settings changed.
- Captions default on as a separate soft file `output/captions.srt` (not burned into
  `final.mp4`). Set `captions.burn_in: true` in an episode `project.yaml` to embed.
  Pin `language: id` for Indo ASR when needed.
- Kids-interest wow scoring prefers payoff moments (animals, reactions) over setup
  walk-ups. Review wow + per-frame narration on the storyboard after `ve preview`.
