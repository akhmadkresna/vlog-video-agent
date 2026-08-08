from __future__ import annotations

from pathlib import Path

import pytest

from vlog_editor.dashboard import approve_plan, require_approval
from vlog_editor.project import DEFAULT_CONFIG, Episode, write_json
from vlog_editor.render import build_render_command


def _episode(tmp_path: Path) -> Episode:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    return Episode(tmp_path, DEFAULT_CONFIG)


def _plan() -> dict:
    return {
        "title": "Test",
        "duration_sec": 2.0,
        "structure": [
            {
                "section": "Hook",
                "clips": [
                    {
                        "file": "clip.mp4",
                        "start": 1.0,
                        "end": 3.0,
                        "note": "test",
                        "subtitle": "",
                    }
                ],
            }
        ],
    }


def test_approval_is_invalidated_by_plan_change(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    approve_plan(episode)
    require_approval(episode)
    changed = _plan()
    changed["title"] = "Changed"
    write_json(episode.plan_path, changed)
    with pytest.raises(PermissionError, match="changed after approval"):
        require_approval(episode)


def test_render_command_builds_nvenc_filter_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    command, filters = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "h264_nvenc" in command
    assert "concat=n=1:v=1:a=1" in filters
    assert "afade=t=in" in filters


def test_bgm_graph_splits_original_audio_before_ducking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    (episode.root / "music.m4a").write_bytes(b"fake")
    episode.config["bgm"]["file"] = "music.m4a"
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "[acat]asplit=2[original][sidechain]" in filters
    assert "[music][sidechain]sidechaincompress" in filters
