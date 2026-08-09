from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

ALLOWED_LICENSES = frozenset(
    {
        "cc0",
        "public_domain",
        "youtube_audio_library",
        "pixabay",
        "original",
        "user_provided",
    }
)
# Pack keys / on-disk folders (meme files live under sfx/meme/).
CLASSIC_SFX_TYPES = ("boing", "pop", "whoosh", "sparkle", "rimshot", "fail", "success")
MEME_ROLES = ("boom", "bruh", "fart", "goofy_laugh", "click")
SFX_TYPES = (*CLASSIC_SFX_TYPES, "meme")
# Cue types emitted into edit plans (classic names + meme roles).
CUE_TYPES = (*CLASSIC_SFX_TYPES, *MEME_ROLES)

DEFAULT_PACK_TEMPLATE = """\
style: funny_kids_indo
license_policy: license_free_only
# Add license-free files under audio/, then list them here.
# Allowed licenses: cc0, public_domain, youtube_audio_library, pixabay,
# original, user_provided (local meme drops under sfx/meme/ with a role).
bgm_candidates: []
sfx:
  boing:
    gain: 0.55
    files: []
  pop:
    gain: 0.50
    files: []
  whoosh:
    gain: 0.45
    files: []
  sparkle:
    gain: 0.40
    files: []
  rimshot:
    gain: 0.50
    files: []
  fail:
    gain: 0.45
    files: []
  success:
    gain: 0.45
    files: []
  meme:
    gain: 0.55
    files: []
"""


def audio_root(episode_root: Path) -> Path:
    return episode_root / "audio"


def bundled_default_audio_root() -> Path:
    return Path(__file__).resolve().parent / "assets" / "default_audio"


def _pack_needs_seed(pack_path: Path) -> bool:
    """Seed only when missing or still the virgin empty template."""
    if not pack_path.is_file():
        return True
    text = pack_path.read_text(encoding="utf-8").strip()
    return text == DEFAULT_PACK_TEMPLATE.strip()


def seed_bundled_default_pack(episode_root: Path, *, force: bool = False) -> bool:
    """Copy researched defaults into episode audio/ for new/virgin packs."""
    bundled = bundled_default_audio_root()
    bundled_pack = bundled / "pack.yaml"
    if not bundled_pack.is_file():
        return False

    root = audio_root(episode_root)
    root.mkdir(parents=True, exist_ok=True)
    pack_path = root / "pack.yaml"
    if not force and not _pack_needs_seed(pack_path):
        return False

    for path in bundled.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "SOURCES.md":
            continue
        relative = path.relative_to(bundled)
        dest = root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if force or not dest.is_file():
            shutil.copy2(path, dest)
    shutil.copy2(bundled_pack, pack_path)
    return True


def ensure_audio_pack_layout(episode_root: Path) -> Path:
    root = audio_root(episode_root)
    (root / "bgm").mkdir(parents=True, exist_ok=True)
    for name in CLASSIC_SFX_TYPES:
        (root / "sfx" / name).mkdir(parents=True, exist_ok=True)
    (root / "sfx" / "meme").mkdir(parents=True, exist_ok=True)
    pack_path = root / "pack.yaml"
    if seed_bundled_default_pack(episode_root):
        return pack_path
    if not pack_path.is_file():
        pack_path.write_text(DEFAULT_PACK_TEMPLATE, encoding="utf-8")
    return pack_path


def _normalize_entry(value: Any, *, default_gain: float = 0.5) -> dict[str, Any] | None:
    if isinstance(value, str):
        return {
            "file": value,
            "license": None,
            "attribution": None,
            "source_url": None,
            "gain": default_gain,
            "role": None,
        }
    if not isinstance(value, dict):
        return None
    file_name = value.get("file")
    if not file_name:
        return None
    role = value.get("role")
    return {
        "file": str(file_name),
        "license": value.get("license"),
        "attribution": value.get("attribution"),
        "source_url": value.get("source_url"),
        "gain": float(value.get("gain", default_gain)),
        "role": str(role).strip().lower() if role else None,
    }


def load_audio_pack(episode_root: Path, pack_rel: str | Path) -> dict[str, Any]:
    pack_path = Path(pack_rel)
    if not pack_path.is_absolute():
        pack_path = episode_root / pack_path
    if not pack_path.is_file():
        return {
            "path": pack_path,
            "style": "funny_kids_indo",
            "license_policy": "license_free_only",
            "bgm_candidates": [],
            "sfx": {name: {"gain": 0.5, "files": []} for name in SFX_TYPES},
            "errors": [],
        }

    raw = yaml.safe_load(pack_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"{pack_path} must contain a mapping")

    errors: list[str] = []
    bgm_candidates: list[dict[str, Any]] = []
    for item in raw.get("bgm_candidates", []) or []:
        entry = _normalize_entry(item, default_gain=0.1)
        if entry is None:
            errors.append("Invalid bgm_candidates entry")
            continue
        license_name = str(entry.get("license") or "").strip().lower()
        if license_name not in ALLOWED_LICENSES:
            errors.append(f"BGM {entry['file']}: license must be one of {sorted(ALLOWED_LICENSES)}")
            continue
        entry["license"] = license_name
        bgm_candidates.append(entry)

    sfx: dict[str, dict[str, Any]] = {}
    raw_sfx = raw.get("sfx", {}) or {}
    for cue_type in SFX_TYPES:
        block = raw_sfx.get(cue_type, {}) or {}
        if not isinstance(block, dict):
            sfx[cue_type] = {"gain": 0.5, "files": []}
            errors.append(f"SFX type {cue_type!r} must be a mapping")
            continue
        default_gain = float(block.get("gain", 0.5))
        files: list[dict[str, Any]] = []
        for item in block.get("files", []) or []:
            entry = _normalize_entry(item, default_gain=default_gain)
            if entry is None:
                errors.append(f"Invalid file entry under sfx.{cue_type}")
                continue
            license_name = str(entry.get("license") or "").strip().lower()
            if license_name not in ALLOWED_LICENSES:
                errors.append(
                    f"SFX {entry['file']}: license must be one of {sorted(ALLOWED_LICENSES)}"
                )
                continue
            entry["license"] = license_name
            if cue_type == "meme":
                role = entry.get("role")
                if role not in MEME_ROLES:
                    errors.append(
                        f"SFX {entry['file']}: meme role must be one of {sorted(MEME_ROLES)}"
                    )
                    continue
            files.append(entry)
        sfx[cue_type] = {"gain": default_gain, "files": files}

    return {
        "path": pack_path,
        "style": str(raw.get("style", "funny_kids_indo")),
        "license_policy": str(raw.get("license_policy", "license_free_only")),
        "bgm_candidates": bgm_candidates,
        "sfx": sfx,
        "errors": errors,
    }


def resolve_pack_file(episode_root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    if relative.startswith("audio/"):
        return episode_root / relative
    return episode_root / "audio" / relative


def meme_files_for_role(pack: dict[str, Any], role: str) -> list[dict[str, Any]]:
    block = pack.get("sfx", {}).get("meme", {}) or {}
    return [
        entry
        for entry in block.get("files", []) or []
        if str(entry.get("role") or "") == role
    ]
