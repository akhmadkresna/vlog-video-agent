from __future__ import annotations

import copy
from pathlib import Path

from vlog_editor.captions import (
    build_caption_cues,
    ffmpeg_subtitles_path,
    prepare_caption_files,
    write_ass,
    write_srt,
)
from vlog_editor.project import DEFAULT_CONFIG, Episode
from vlog_editor.render import build_render_command


def test_build_caption_cues_maps_words_onto_edit_timeline() -> None:
    analysis = {
        "clips": [
            {
                "metadata": {"filename": "a.mp4", "duration": 20},
                "audio": {
                    "words": [
                        {"start": 1.0, "end": 1.4, "text": "Hai"},
                        {"start": 1.4, "end": 2.2, "text": "Assalamualaikum"},
                        {"start": 2.2, "end": 3.0, "text": "pagi"},
                        {"start": 3.0, "end": 3.6, "text": "ini"},
                        {"start": 10.0, "end": 10.5, "text": "skip"},
                    ]
                },
            }
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Open",
                "clips": [{"file": "a.mp4", "start": 1.0, "end": 4.0}],
            }
        ]
    }
    cues = build_caption_cues(plan, analysis, max_chars=40, max_lines=2)
    assert cues
    assert cues[0]["start"] == 0.0
    assert "Assalamualaikum" in cues[0]["text"]
    assert all("skip" not in cue["text"] for cue in cues)


def test_write_srt_and_ass_roundtrip(tmp_path: Path) -> None:
    cues = [{"start": 0.22, "end": 3.5, "text": "Hai Assalamualaikum\npagi ini"}]
    srt = write_srt(cues, tmp_path / "captions.srt")
    ass = write_ass(cues, tmp_path / "captions.ass", width=1920, height=1080)
    srt_text = srt.read_text(encoding="utf-8")
    ass_text = ass.read_text(encoding="utf-8")
    assert "00:00:00,220 --> 00:00:03,500" in srt_text
    assert "Hai Assalamualaikum" in srt_text
    assert "Dialogue: 0,0:00:00.22,0:00:03.50,Default," in ass_text
    assert r"\N" in ass_text


def test_ffmpeg_subtitles_path_escapes_windows_drive(tmp_path: Path) -> None:
    path = tmp_path / "captions.ass"
    path.write_text("", encoding="utf-8")
    escaped = ffmpeg_subtitles_path(path)
    assert "captions.ass" in escaped
    # Drive letter paths need an escaped colon for ffmpeg filter graphs.
    if len(str(path.resolve())) >= 2 and str(path.resolve())[1] == ":":
        assert "\\:" in escaped


def test_prepare_caption_files_and_burn_in_filter(
    tmp_path: Path, monkeypatch
) -> None:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    (tmp_path / "footage" / "clip.mp4").write_bytes(b"fake")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["captions"] = {"enabled": True, "burn_in": True, "font_size": 42}
    episode = Episode(tmp_path, config)
    analysis = {
        "clips": [
            {
                "metadata": {"filename": "clip.mp4", "duration": 5},
                "audio": {
                    "words": [
                        {"start": 0.0, "end": 0.5, "text": "Halo"},
                        {"start": 0.5, "end": 1.2, "text": "semua"},
                    ]
                },
            }
        ]
    }
    plan = {
        "title": "Test",
        "duration_sec": 2.0,
        "structure": [
            {
                "section": "Hook",
                "clips": [
                    {
                        "file": "clip.mp4",
                        "start": 0.0,
                        "end": 2.0,
                        "note": "",
                        "subtitle": "",
                    }
                ],
            }
        ],
    }
    meta = prepare_caption_files(episode, plan, analysis)
    assert meta["enabled"] is True
    assert meta["cues"] >= 1
    assert Path(meta["srt_path"]).is_file()
    ass_path = Path(meta["ass_path"])
    assert ass_path.is_file()

    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters, _ = build_render_command(
        episode,
        plan,
        episode.output / "final.mp4",
        burn_in_captions=ass_path,
    )
    assert "[vcat]ass=" in filters
    assert "[vcap]" in filters
