from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from vlog_editor.media import list_videos, probe_video
from vlog_editor.project import Episode

DJI_DATETIME_RE = re.compile(r"DJI_(\d{8})(\d{6})", re.IGNORECASE)
GENERIC_DATETIME_RE = re.compile(
    r"(?P<date>\d{4}[-_]\d{2}[-_]\d{2})[ T_-]+"
    r"(?P<hour>\d{2})[-_.:](?P<minute>\d{2})[-_.:](?P<second>\d{2})"
)


def filename_start_time(filename: str) -> datetime | None:
    """Parse local recording start from DJI or common screen-recorder filenames."""
    dji = DJI_DATETIME_RE.search(filename)
    if dji:
        try:
            return datetime.strptime("".join(dji.groups()), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    generic = GENERIC_DATETIME_RE.search(filename)
    if not generic:
        return None
    stamp = (
        generic.group("date").replace("_", "-")
        + " "
        + ":".join(
            (
                generic.group("hour"),
                generic.group("minute"),
                generic.group("second"),
            )
        )
    )
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _enabled(episode: Episode) -> bool:
    raw = episode.config.get("gameplay", {}).get("enabled", "auto")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"false", "off", "no", "0", "disabled"}


def gameplay_files(episode: Episode) -> list[Path]:
    if not _enabled(episode):
        return []
    directory = str(episode.config.get("gameplay", {}).get("directory", "gameplay"))
    root = Path(directory)
    if not root.is_absolute():
        root = episode.root / root
    return list_videos(root)


def match_gameplay(
    episode: Episode,
    camera_file: str,
    camera_start: float,
    camera_end: float,
) -> dict[str, Any] | None:
    """Find gameplay overlapping a selected camera range by local wall-clock time."""
    camera_path = episode.footage / camera_file
    camera_clock = filename_start_time(camera_file)
    if camera_clock is None or not camera_path.is_file():
        return None

    config = episode.config.get("gameplay", {})
    min_overlap = float(config.get("min_overlap_sec", 2.0))
    selected_start = camera_clock.timestamp() + camera_start
    selected_end = camera_clock.timestamp() + camera_end
    best: tuple[float, dict[str, Any]] | None = None
    for game_path in gameplay_files(episode):
        game_clock = filename_start_time(game_path.name)
        if game_clock is None:
            continue
        game_duration = float(probe_video(game_path)["duration"])
        game_start = game_clock.timestamp()
        game_end = game_start + game_duration
        overlap_start = max(selected_start, game_start)
        overlap_end = min(selected_end, game_end)
        overlap = overlap_end - overlap_start
        if overlap < min_overlap:
            continue
        payload = {
            "file": game_path.name,
            "mode": "game_main_camera_pip",
            "camera_offset_sec": round(overlap_start - selected_start, 3),
            "game_start_sec": round(overlap_start - game_start, 3),
            "duration_sec": round(overlap, 3),
            "audio_source": str(config.get("audio_source", "camera")),
        }
        if best is None or overlap > best[0]:
            best = overlap, payload
    return best[1] if best else None


def attach_gameplay_matches(episode: Episode, plan: dict[str, Any]) -> dict[str, Any]:
    """Annotate selected camera cuts with synchronized gameplay/PIP instructions."""
    files = gameplay_files(episode)
    matched_files: set[str] = set()
    matched_cuts = 0
    for section in plan.get("structure", []):
        for clip in section.get("clips", []):
            clip.pop("gameplay", None)
            match = match_gameplay(
                episode,
                str(clip.get("file", "")),
                float(clip.get("start", 0)),
                float(clip.get("end", 0)),
            )
            if match:
                clip["gameplay"] = match
                matched_files.add(str(match["file"]))
                matched_cuts += 1
    plan["gameplay_summary"] = {
        "files": len(files),
        "matched_files": len(matched_files),
        "matched_cuts": matched_cuts,
        "unmatched_files": [path.name for path in files if path.name not in matched_files],
    }
    return plan
