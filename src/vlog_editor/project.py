from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Music sits under family dialogue, not beside it. Dialogue is peak-normalized near
# full scale, so a bed above ~0.35 competes with speech even while ducking works.
DEFAULT_BGM_VOLUME = 0.22

DEFAULT_CONFIG: dict[str, Any] = {
    "title": "My Vlog",
    "language": "auto",
    "target_duration_sec": "auto",
    "setting_order": [],
    "style": "warm cinematic travel vlog",
    "output": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "video_codec": "auto",
    },
    "asr": {
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "int8_float16",
    },
    "vision": {
        "endpoint": "http://127.0.0.1:11434",
        "model": "qwen3-vl:4b-instruct",
        "frame_width": 1280,
        "max_output_tokens": 512,
        "keep_alive": "30m",
    },
    "audio": {
        "enabled": True,
        "style": "funny_kids_indo",
        "sfx_density": "medium",
        "pack": "audio/pack.yaml",
        "license_policy": "license_free_only",
    },
    "bgm": {
        "file": None,
        "volume": DEFAULT_BGM_VOLUME,
        # beds = intro / play / light B-roll / outro (default). full = whole edit.
        "mode": "beds",
    },
    "gameplay": {
        # Put screen/game recordings in gameplay/. "auto" enables matching when present.
        "enabled": "auto",
        "directory": "gameplay",
        # Camera audio keeps reactions/lips synchronized; gameplay is visual-only.
        "audio_source": "camera",
        "min_overlap_sec": 2.0,
        "pip": {
            "width_ratio": 0.28,
            "margin": 40,
            "border": 6,
            "position": "bottom_right",
        },
    },
    "captions": {
        # Soft sidecar SRT by default — ASR (faster-whisper large-v3-turbo,
        # int8_float16) isn't accurate enough to bake permanently into every
        # render. Set burn_in true per episode once transcript quality is
        # confirmed for that footage.
        "enabled": True,
        "burn_in": False,
        "max_chars": 42,
        "max_lines": 2,
        "max_cue_sec": 4.5,
        "font_name": "Arial",
        "font_size": 64,
        "bold": True,
        "margin_v": 64,
        # Karaoke burn-in: words highlight in primary_color as they're spoken,
        # outlined in outline_color. Set karaoke false for plain static captions.
        "karaoke": True,
        "primary_color": "#FF2D78",
        "secondary_color": "#FFFFFF",
        "outline_color": "#FFFFFF",
        "outline_width": 4,
    },
    "transitions": {
        # Fixed "few minutes later" card image, spliced in at render time —
        # snapped to the nearest clip boundary at/after each interval, skipped
        # near the very end of the edit.
        "enabled": True,
        "interval_sec": 300.0,
        "card_duration": 2.0,
        # Punchy pop as each card appears (bundled meme boom by default).
        # Set "" to disable, or any other audio-pack cue type/role.
        "sfx_type": "boom",
        "sfx_gain": None,
    },
    "youtube": {
        "privacy": "unlisted",
        "category_id": "24",
        "made_for_kids": True,
        "kids_destination": True,
        "notify_subscribers": False,
        "upload_captions": True,
        "title": None,
        "description": None,
        "tags": [],
        "playlist_id": None,
        "contains_synthetic_media": False,
        "paid_promotion": False,
        "embeddable": True,
    },
    # Default story shape for balanced plans. Override per episode in project.yaml.
    # chronological = full capture-time order (default: simplest, matches how the day happened)
    # scene_energy = time-of-day scenes (Morning/Afternoon/Evening/Night) in capture order; best→better inside
    # kids_energy = open → global kids peak → peak 2 → quiet/adult → goodbye
    "story_arc": "chronological",
    # primary_day = plan from the heaviest capture day only (default).
    # all_days = every dated day, assembled as one composite day (morning → night).
    "planning_scope": "primary_day",
}


@dataclass(frozen=True)
class Episode:
    root: Path
    config: dict[str, Any]

    @property
    def footage(self) -> Path:
        return self.root / "footage"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def analysis_path(self) -> Path:
        return self.work / "clip_analysis.json"

    @property
    def plan_path(self) -> Path:
        return self.work / "edit_plan.json"

    @property
    def approval_path(self) -> Path:
        return self.work / "approval.json"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def gameplay(self) -> Path:
        directory = Path(str(self.config.get("gameplay", {}).get("directory", "gameplay")))
        return directory if directory.is_absolute() else self.root / directory


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_episode(path: str | Path) -> Episode:
    root = Path(path).expanduser().resolve()
    config_path = root / "project.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing {config_path}. Run `ve new {root}` first.")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError("project.yaml must contain a mapping")
    return Episode(root=root, config=_deep_merge(DEFAULT_CONFIG, raw))


def create_episode(path: str | Path, *, force: bool = False) -> Episode:
    from vlog_editor.audio_pack import ensure_audio_pack_layout

    root = Path(path).expanduser().resolve()
    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(f"Refusing to overwrite non-empty directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("footage", "gameplay", "work", "output"):
        (root / name).mkdir(exist_ok=True)
    ensure_audio_pack_layout(root)
    config_path = root / "project.yaml"
    if force or not config_path.exists():
        config_path.write_text(
            yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return resolve_episode(root)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
