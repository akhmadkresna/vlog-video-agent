from __future__ import annotations

import copy
from pathlib import Path

from vlog_editor import gameplay
from vlog_editor.gameplay import attach_gameplay_matches, filename_start_time, match_gameplay
from vlog_editor.project import DEFAULT_CONFIG, Episode


def _episode(tmp_path: Path) -> Episode:
    for name in ("footage", "gameplay", "work", "output"):
        (tmp_path / name).mkdir()
    return Episode(tmp_path, copy.deepcopy(DEFAULT_CONFIG))


def test_filename_start_time_supports_camera_and_game_capture() -> None:
    assert filename_start_time("DJI_20260815094942_0037_D.MP4").isoformat() == (
        "2026-08-15T09:49:42"
    )
    assert filename_start_time("Disney Speedstorm 2026-08-15 09-49-56.mp4").isoformat() == (
        "2026-08-15T09:49:56"
    )


def test_match_gameplay_uses_wall_clock_overlap(
    tmp_path: Path, monkeypatch
) -> None:
    episode = _episode(tmp_path)
    camera = episode.footage / "DJI_20260815094942_0037_D.MP4"
    game = episode.gameplay / "Disney Speedstorm 2026-08-15 09-49-56.mp4"
    camera.write_bytes(b"camera")
    game.write_bytes(b"game")
    monkeypatch.setattr(gameplay, "gameplay_files", lambda _: [game])
    monkeypatch.setattr(gameplay, "probe_video", lambda _: {"duration": 446.0})

    match = match_gameplay(episode, camera.name, 0.0, 60.0)
    assert match is not None
    assert match["camera_offset_sec"] == 14.0
    assert match["game_start_sec"] == 0.0
    assert match["duration_sec"] == 46.0


def test_attach_gameplay_reports_unmatched_files(
    tmp_path: Path, monkeypatch
) -> None:
    episode = _episode(tmp_path)
    camera = episode.footage / "DJI_20260815094942_0037_D.MP4"
    matched = episode.gameplay / "Disney Speedstorm 2026-08-15 09-49-56.mp4"
    unmatched = episode.gameplay / "Disney Speedstorm 2026-08-15 11-26-25.mp4"
    camera.write_bytes(b"camera")
    matched.write_bytes(b"game")
    unmatched.write_bytes(b"game")
    monkeypatch.setattr(gameplay, "gameplay_files", lambda _: [matched, unmatched])
    monkeypatch.setattr(gameplay, "probe_video", lambda _: {"duration": 446.0})
    plan = {
        "structure": [
            {
                "section": "Gaming",
                "clips": [{"file": camera.name, "start": 0.0, "end": 60.0}],
            }
        ]
    }
    attached = attach_gameplay_matches(episode, plan)
    assert attached["structure"][0]["clips"][0]["gameplay"]["file"] == matched.name
    assert attached["gameplay_summary"]["matched_files"] == 1
    assert attached["gameplay_summary"]["unmatched_files"] == [unmatched.name]
