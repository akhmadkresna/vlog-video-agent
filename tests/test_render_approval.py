from __future__ import annotations

import copy
from pathlib import Path

import pytest

from vlog_editor.dashboard import approve_plan, require_approval
from vlog_editor.project import DEFAULT_BGM_VOLUME, DEFAULT_CONFIG, Episode, write_json
from vlog_editor.render import build_render_command


def _episode(tmp_path: Path) -> Episode:
    for name in ("footage", "gameplay", "work", "output"):
        (tmp_path / name).mkdir()
    return Episode(tmp_path, copy.deepcopy(DEFAULT_CONFIG))


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
    command, filters, _ = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "h264_nvenc" in command
    assert "concat=n=1:v=1:a=1" in filters
    assert "afade=t=in" in filters
    # Speech clarity chain: rumble filter + leveling per clip, loudness anchor after concat.
    assert "highpass=f=85" in filters
    assert "acompressor=" in filters
    assert "loudnorm=I=-15:TP=-1.5:LRA=11" in filters


def test_render_uses_portable_filtergraph_and_gpu_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    command, _filters, _ = build_render_command(episode, _plan(), episode.output / "final.mp4")
    # -filter_complex_script was removed in ffmpeg 7.0; must use the -/opt form.
    assert "-/filter_complex" in command
    assert "-filter_complex_script" not in command
    # 4K/60 HEVC is unwatchably slow to software-decode: GPU decode by default.
    assert "-hwaccel" in command
    assert command[command.index("-hwaccel") + 1] == "cuda"
    # -hwaccel is an input option: it must appear before the -i it applies to.
    assert command.index("-hwaccel") < command.index("-i")


def test_render_hwaccel_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    episode.config["output"]["hwaccel"] = "none"
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    command, _filters, _ = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "-hwaccel" not in command


def test_gameplay_layer_fills_frame_and_honours_source_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    episode.config["gameplay"]["source_crop"] = "1222:720:58:0"
    (episode.footage / "clip.mp4").write_bytes(b"camera")
    (episode.gameplay / "game.mp4").write_bytes(b"game")
    plan = _plan()
    plan["structure"][0]["clips"][0]["gameplay"] = {
        "file": "game.mp4",
        "mode": "game_main_camera_pip",
        "camera_offset_sec": 0.0,
        "game_start_sec": 0.0,
        "duration_sec": 2.0,
        "audio_source": "camera",
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    _command, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    game_line = next(line for line in filters.split(";\n") if line.strip().startswith("[1:v]"))
    # literal crop applied, then cover-scale + centre-crop so no black bar shows
    assert "crop=1222:720:58:0" in game_line
    assert "force_original_aspect_ratio=increase" in game_line
    assert f"crop={episode.config['output']['width']}:{episode.config['output']['height']}" in game_line
    # camera layer still letterbox-safe (never crop faces)
    cam_line = next(line for line in filters.split(";\n") if line.strip().startswith("[0:v]"))
    assert "force_original_aspect_ratio=decrease" in cam_line


def test_gameplay_source_crop_can_be_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    episode.config["gameplay"]["source_crop"] = "none"
    (episode.footage / "clip.mp4").write_bytes(b"camera")
    (episode.gameplay / "game.mp4").write_bytes(b"game")
    plan = _plan()
    plan["structure"][0]["clips"][0]["gameplay"] = {
        "file": "game.mp4",
        "mode": "game_main_camera_pip",
        "camera_offset_sec": 0.0,
        "game_start_sec": 0.0,
        "duration_sec": 2.0,
        "audio_source": "camera",
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    _command, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    game_line = next(line for line in filters.split(";\n") if line.strip().startswith("[1:v]"))
    assert "crop=1222" not in game_line
    # still fills the frame, just without a source-border trim
    assert "force_original_aspect_ratio=increase" in game_line


def test_render_command_builds_game_main_with_camera_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"camera")
    (episode.gameplay / "game.mp4").write_bytes(b"game")
    plan = _plan()
    plan["structure"][0]["clips"][0]["gameplay"] = {
        "file": "game.mp4",
        "mode": "game_main_camera_pip",
        "camera_offset_sec": 0.5,
        "game_start_sec": 3.0,
        "duration_sec": 1.0,
        "audio_source": "camera",
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    command, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    assert str(episode.gameplay / "game.mp4") in command
    assert "split=2[cam_base0][cam_pip0]" in filters
    assert "[0:v]" in filters
    assert "[1:v]" in filters
    assert "overlay=0:0:eof_action=pass" in filters
    assert "overlay=1330:726:eof_action=pass" in filters
    # Audio remains camera input 0; gameplay input 1 is visual-only.
    assert "[0:a]aresample=48000" in filters
    assert "[1:a]aresample=48000" not in filters


def test_render_intro_holds_full_cam_then_dissolves_to_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    episode.config["gameplay"]["intro"] = {"cam_hold_sec": 3.0, "transition_sec": 1.0}
    (episode.footage / "clip.mp4").write_bytes(b"camera")
    (episode.gameplay / "game.mp4").write_bytes(b"game")
    plan = _plan()
    plan["structure"][0]["clips"][0]["gameplay"] = {
        "file": "game.mp4",
        "mode": "game_main_camera_pip",
        "camera_offset_sec": 0.0,
        "game_start_sec": 0.0,
        "duration_sec": 10.0,
        "audio_source": "camera",
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    _command, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    # game + PIP are baked into one layer whose alpha fades in after the hold
    assert "[game0][pip0]overlay=1330:726:eof_action=pass[compo0]" in filters
    assert "fade=t=in:st=3.000:d=1.000:alpha=1[compo_fade0]" in filters
    assert "[cam_base0][compo_fade0]overlay=0:0:eof_action=pass:" in filters
    assert "enable='between(t,3.000,10.000)'[v0]" in filters
    # the plain (non-intro) game_base path is not used for the intro cut
    assert "game_base0" not in filters


def test_intro_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"camera")
    (episode.gameplay / "game.mp4").write_bytes(b"game")
    plan = _plan()
    plan["structure"][0]["clips"][0]["gameplay"] = {
        "file": "game.mp4",
        "mode": "game_main_camera_pip",
        "camera_offset_sec": 0.5,
        "game_start_sec": 3.0,
        "duration_sec": 1.0,
        "audio_source": "camera",
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("h264_nvenc", ["-cq", "19"]))
    _command, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    assert "compo0" not in filters
    assert "game_base0" in filters


def test_bgm_graph_splits_original_audio_before_ducking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    (episode.root / "music.m4a").write_bytes(b"fake")
    episode.config["bgm"]["file"] = "music.m4a"
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters, _ = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "loudnorm=I=-15:TP=-1.5:LRA=11" in filters
    assert "[dialogue_norm]asplit=2[original][sidechain]" in filters
    assert (
        "[music][sidechain]sidechaincompress=threshold=0.05:ratio=12:"
        "attack=8:release=320:makeup=1:mix=1[ducked]"
    ) in filters


def test_bgm_ducking_never_applies_makeup_gain_to_music(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Makeup gain re-raises the whole bed above dialogue and undoes the duck."""
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    (episode.root / "music.m4a").write_bytes(b"fake")
    episode.config["bgm"]["file"] = "music.m4a"
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters, _ = build_render_command(episode, _plan(), episode.output / "final.mp4")
    assert "makeup=1:" in filters
    for boosted in ("makeup=1.5", "makeup=2", "makeup=2.0", "makeup=3"):
        assert boosted not in filters
    # Full wet duck: no dry music path bypassing the compressor under speech.
    assert "mix=0.55" not in filters


def test_default_bgm_volume_sits_under_dialogue(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    assert float(episode.config["bgm"]["volume"]) == DEFAULT_BGM_VOLUME
    assert DEFAULT_BGM_VOLUME <= 0.35


def test_bgm_beds_use_adelay_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    (episode.root / "music.m4a").write_bytes(b"fake")
    plan = _plan()
    plan["bgm"] = {
        "file": "music.m4a",
        "volume": 0.12,
        "license": "original",
        "mode": "beds",
        "segments": [
            {
                "start_sec": 0.0,
                "end_sec": 1.0,
                "volume": 0.18,
                "reason": "intro",
                "file": "music.m4a",
                "license": "original",
            },
            {
                "start_sec": 3.0,
                "end_sec": 5.0,
                "volume": 0.14,
                "reason": "playing / fooling around",
                "file": "music.m4a",
                "license": "original",
            },
        ],
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    assert "adelay=0|0" in filters
    assert "adelay=3000|3000" in filters
    assert "amix=inputs=2:duration=longest:normalize=0,apad=whole_dur=" in filters
    assert "[music]" in filters
    # Individual beds delay only; full-timeline pad happens once on the mix.
    assert "adelay=0|0,apad=whole_dur=" not in filters
    assert "adelay=3000|3000,apad=whole_dur=" not in filters
