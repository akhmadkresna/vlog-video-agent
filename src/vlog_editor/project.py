from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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
        "max_output_tokens": 240,
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
        "volume": 0.24,
        # beds = intro / play / light B-roll / outro (default). full = whole edit.
        "mode": "beds",
    },
    "captions": {
        # Soft sidecar SRT by default; set burn_in true per episode to embed on video.
        "enabled": True,
        "burn_in": False,
        "max_chars": 42,
        "max_lines": 2,
        "max_cue_sec": 4.5,
        "font_name": "Arial",
        "font_size": 48,
        "margin_v": 64,
    },
    # Default story shape for balanced plans. Override per episode in project.yaml.
    # scene_energy = contiguous same-setting scenes in capture order; best→better inside
    # kids_energy = open → global kids peak → peak 2 → quiet/adult → goodbye
    # chronological = full capture-time order
    "story_arc": "scene_energy",
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
    for name in ("footage", "work", "output"):
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
