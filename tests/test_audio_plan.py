from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from vlog_editor.audio_pack import (
    bundled_default_audio_root,
    ensure_audio_pack_layout,
    load_audio_pack,
)
from vlog_editor.audio_plan import plan_audio_cues
from vlog_editor.project import DEFAULT_CONFIG, Episode, create_episode
from vlog_editor.render import build_render_command
from vlog_editor.validation import validate_audio_plan


def _episode(tmp_path: Path) -> Episode:
    episode = create_episode(tmp_path / "trip")
    return episode


def _analysis() -> dict:
    return {
        "clips": [
            {
                "metadata": {
                    "filename": "home.mp4",
                    "duration": 20,
                    "capture_time": "2026-08-01T01:00:00.000000Z",
                },
                "audio": {
                    "text": "lucu ya adek ketawa",
                    "words": [
                        {"start": 1.0, "end": 1.2, "text": "lucu"},
                        {"start": 1.3, "end": 1.5, "text": "ya"},
                    ],
                    "segments": [{"start": 1.0, "end": 3.0, "text": "lucu ya adek ketawa"}],
                },
                "visual": {
                    "setting": "home",
                    "summary": "child laughs at home",
                    "story_value": 0.9,
                    "quality": 0.8,
                },
            },
            {
                "metadata": {
                    "filename": "store.mp4",
                    "duration": 20,
                    "capture_time": "2026-08-01T06:00:00.000000Z",
                },
                "audio": {"text": "belanja", "words": [], "segments": []},
                "visual": {
                    "setting": "store",
                    "summary": "store aisle",
                    "story_value": 0.7,
                    "quality": 0.8,
                },
            },
        ]
    }


def _plan() -> dict:
    return {
        "title": "Trip",
        "duration_sec": 16.0,
        "editing_notes": "test",
        "structure": [
            {
                "section": "Home — Best moments",
                "clips": [
                    {
                        "file": "home.mp4",
                        "start": 0,
                        "end": 8,
                        "note": "child laughs",
                        "subtitle": "lucu ya adek ketawa",
                    }
                ],
            },
            {
                "section": "Store — Best moments",
                "clips": [
                    {
                        "file": "store.mp4",
                        "start": 0,
                        "end": 8,
                        "note": "store aisle",
                        "subtitle": "belanja",
                    }
                ],
            },
        ],
    }


def test_create_episode_seeds_bundled_defaults(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    assert (episode.audio / "pack.yaml").is_file()
    assert (episode.audio / "sfx" / "boing").is_dir()
    assert (episode.audio / "sfx" / "meme").is_dir()
    assert (episode.audio / "sfx" / "hype_voice").is_dir()
    pack = load_audio_pack(episode.root, "audio/pack.yaml")
    assert pack["errors"] == []
    assert pack["sfx"]["boing"]["files"]
    assert pack["sfx"]["meme"]["files"]
    # Kid-voice hype ships with real CC0 kid-laughter clips for `yay` only;
    # the spoken-word roles ship empty (see SOURCES.md for why) and the
    # planner falls back to classic/meme cues until the user drops files in.
    hype_roles = {entry["role"] for entry in pack["sfx"]["hype_voice"]["files"]}
    assert hype_roles == {"yay"}
    assert all(
        entry["license"] == "cc0" for entry in pack["sfx"]["hype_voice"]["files"]
    )
    for entry in pack["sfx"]["hype_voice"]["files"]:
        assert (episode.root / "audio" / entry["file"]).is_file()
    assert {entry["role"] for entry in pack["sfx"]["meme"]["files"]} >= {
        "boom",
        "bruh",
        "click",
        "goofy_laugh",
    }
    assert len(pack["bgm_candidates"]) >= 3
    assert pack["bgm_candidates"][0]["license"] == "pixabay"
    assert (episode.root / "audio" / pack["bgm_candidates"][0]["file"]).is_file()
    sample = pack["sfx"]["meme"]["files"][0]
    assert sample["license"] == "user_provided"
    assert (episode.root / "audio" / sample["file"]).is_file()
    assert (bundled_default_audio_root() / "pack.yaml").is_file()


def test_load_pack_rejects_disallowed_license(tmp_path: Path) -> None:
    pack_path = ensure_audio_pack_layout(tmp_path)
    pack_path.write_text(
        yaml.safe_dump(
            {
                "license_policy": "license_free_only",
                "bgm_candidates": [],
                "sfx": {
                    "boing": {
                        "gain": 0.5,
                        "files": [
                            {
                                "file": "sfx/boing/a.wav",
                                "license": "epidemic_sound",
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    pack = load_audio_pack(tmp_path, "audio/pack.yaml")
    assert pack["errors"]
    assert any("license must be one of" in error for error in pack["errors"])


def test_cue_planner_places_whoosh_and_sparkle_with_license_free_files(
    tmp_path: Path,
) -> None:
    episode = _episode(tmp_path)
    sparkle = episode.audio / "sfx" / "sparkle" / "sparkle_01.wav"
    whoosh = episode.audio / "sfx" / "whoosh" / "whoosh_01.wav"
    sparkle.write_bytes(b"fake")
    whoosh.write_bytes(b"fake")
    (episode.audio / "pack.yaml").write_text(
        yaml.safe_dump(
            {
                "style": "funny_kids_indo",
                "license_policy": "license_free_only",
                "bgm_candidates": [],
                "sfx": {
                    "sparkle": {
                        "gain": 0.4,
                        "files": [
                            {
                                "file": "sfx/sparkle/sparkle_01.wav",
                                "license": "cc0",
                            }
                        ],
                    },
                    "whoosh": {
                        "gain": 0.45,
                        "files": [
                            {
                                "file": "sfx/whoosh/whoosh_01.wav",
                                "license": "public_domain",
                            }
                        ],
                    },
                    "boing": {"gain": 0.5, "files": []},
                    "pop": {"gain": 0.5, "files": []},
                    "rimshot": {"gain": 0.5, "files": []},
                    "fail": {"gain": 0.5, "files": []},
                    "success": {"gain": 0.5, "files": []},
                    "meme": {"gain": 0.55, "files": []},
                },
            }
        ),
        encoding="utf-8",
    )
    planned = plan_audio_cues(episode, _plan(), _analysis())
    cues = planned["audio_cues"]
    assert cues
    assert any(cue["type"] == "whoosh" for cue in cues)
    # Without meme laugh files, classic sparkle still wins for laugh moments.
    assert any(cue["type"] == "sparkle" for cue in cues)
    assert all(cue["license"] in {"cc0", "public_domain"} for cue in cues)
    errors = validate_audio_plan(planned, episode.root, min_gap=6.0)
    assert errors == []


def test_cue_planner_prefers_meme_roles_with_fallbacks(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    meme_dir = episode.audio / "sfx" / "meme"
    meme_dir.mkdir(parents=True, exist_ok=True)
    for name in ("click.mp3", "laugh.mp3", "bruh.mp3"):
        (meme_dir / name).write_bytes(b"fake")
    (episode.audio / "pack.yaml").write_text(
        yaml.safe_dump(
            {
                "style": "funny_kids_indo",
                "license_policy": "license_free_only",
                "bgm_candidates": [],
                "sfx": {
                    "boing": {"gain": 0.5, "files": []},
                    "pop": {"gain": 0.5, "files": []},
                    "whoosh": {"gain": 0.5, "files": []},
                    "sparkle": {"gain": 0.5, "files": []},
                    "rimshot": {"gain": 0.5, "files": []},
                    "fail": {"gain": 0.5, "files": []},
                    "success": {"gain": 0.5, "files": []},
                    "meme": {
                        "gain": 0.55,
                        "files": [
                            {
                                "file": "sfx/meme/click.mp3",
                                "role": "click",
                                "license": "user_provided",
                            },
                            {
                                "file": "sfx/meme/laugh.mp3",
                                "role": "goofy_laugh",
                                "license": "user_provided",
                            },
                            {
                                "file": "sfx/meme/bruh.mp3",
                                "role": "bruh",
                                "license": "user_provided",
                            },
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    planned = plan_audio_cues(episode, _plan(), _analysis())
    cues = planned["audio_cues"]
    types = {cue["type"] for cue in cues}
    assert "click" in types or "boom" in types or "goofy_laugh" in types
    assert "goofy_laugh" in types
    assert all(cue["license"] == "user_provided" for cue in cues)
    errors = validate_audio_plan(planned, episode.root, min_gap=6.0)
    assert errors == []


def test_cue_planner_uses_hype_voice_on_kids_energy_moments(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    hype_dir = episode.audio / "sfx" / "hype_voice"
    hype_dir.mkdir(parents=True, exist_ok=True)
    (hype_dir / "woohoo.mp3").write_bytes(b"fake")
    pack_path = episode.audio / "pack.yaml"
    pack_data = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_data["sfx"]["hype_voice"] = {
        "gain": 0.35,
        "files": [
            {
                "file": "sfx/hype_voice/woohoo.mp3",
                "role": "woohoo",
                "license": "user_provided",
                "gain": 0.3,
            }
        ],
    }
    pack_path.write_text(yaml.safe_dump(pack_data), encoding="utf-8")

    analysis = _analysis()
    # Avoid laugh/cute trigger words so the new kids-interest branch is what fires.
    analysis["clips"][0]["visual"]["summary"] = "playground slide equipment"
    analysis["clips"][0]["visual"]["story_value"] = 0.65
    analysis["clips"][0]["audio"]["text"] = "seru banget naik ayunan di playground"
    plan = _plan()
    plan["structure"][0]["clips"][0]["note"] = "playground slide equipment"
    plan["structure"][0]["clips"][0]["subtitle"] = "seru banget naik ayunan di playground"

    planned = plan_audio_cues(episode, plan, analysis)
    cues = planned["audio_cues"]
    hype_cues = [cue for cue in cues if cue["type"] == "woohoo"]
    assert hype_cues
    assert hype_cues[0]["role"] == "woohoo"
    # Stays quiet relative to meme gains (0.55) so it doesn't talk over kids.
    assert hype_cues[0]["gain"] <= 0.4
    errors = validate_audio_plan(planned, episode.root, min_gap=6.0)
    assert errors == []


def test_validate_audio_plan_rejects_missing_file_and_bad_license(tmp_path: Path) -> None:
    plan = _plan()
    plan["audio_cues"] = [
        {
            "at_sec": 1.0,
            "type": "boing",
            "file": "audio/sfx/boing/missing.wav",
            "gain": 0.5,
            "license": "paid_stock",
            "reason": "test",
        }
    ]
    errors = validate_audio_plan(plan, tmp_path, min_gap=6.0)
    assert any("license must be one of" in error for error in errors)
    assert any("missing file" in error for error in errors)


def test_empty_pack_leaves_plan_without_cues(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    for path in episode.audio.rglob("*"):
        if path.is_file():
            path.unlink()
    # Intentional empty pack (not the virgin template) — do not re-seed.
    (episode.audio / "pack.yaml").write_text(
        yaml.safe_dump(
            {
                "style": "funny_kids_indo",
                "license_policy": "license_free_only",
                "bgm_candidates": [],
                "sfx": {
                    name: {"gain": 0.5, "files": []}
                    for name in (
                        "boing",
                        "pop",
                        "whoosh",
                        "sparkle",
                        "rimshot",
                        "fail",
                        "success",
                        "meme",
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    planned = plan_audio_cues(episode, _plan(), _analysis())
    assert planned.get("audio_cues") == []


def test_seeded_defaults_produce_license_free_cues(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    planned = plan_audio_cues(episode, _plan(), _analysis())
    cues = planned.get("audio_cues") or []
    assert cues
    # Default pack uses meme click/boom for transitions; yay (bundled kid
    # laughter) wins over goofy_laugh/sparkle for laughs since it's tried first.
    assert any(cue["type"] in {"click", "boom", "whoosh"} for cue in cues)
    assert any(cue["type"] in {"yay", "goofy_laugh", "sparkle"} for cue in cues)
    assert all(cue["license"] in {"cc0", "user_provided"} for cue in cues)
    bgm = planned.get("bgm")
    assert isinstance(bgm, dict)
    assert bgm["license"] in {"cc0", "pixabay", "public_domain", "youtube_audio_library"}
    assert bgm.get("mode") == "beds"
    assert bgm.get("segments")
    # Laugh/play clips cover the open, so intro may be omitted on purpose.
    assert any(
        float(seg.get("start_sec", 99)) <= 0.5
        or "intro" in str(seg.get("reason", ""))
        for seg in bgm["segments"]
    )
    assert all(seg.get("file") for seg in bgm["segments"])
    errors = validate_audio_plan(planned, episode.root, min_gap=6.0)
    assert errors == []


def test_bgm_beds_include_playing_and_skip_full_coverage(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    analysis = _analysis()
    analysis["clips"][0]["visual"]["summary"] = "anak bermain dan ketawa di rumah"
    analysis["clips"][0]["audio"]["words"] = []
    plan = _plan()
    plan["structure"][0]["clips"][0]["note"] = "anak mainan lucu"
    plan["structure"][0]["clips"][0]["subtitle"] = "seru main bareng"
    # Make the play clip longer so the bed is capped, not full-edit.
    plan["structure"][0]["clips"][0]["end"] = 30
    plan["duration_sec"] = 38.0
    plan["structure"][1]["clips"][0]["start"] = 0
    plan["structure"][1]["clips"][0]["end"] = 8
    planned = plan_audio_cues(episode, plan, analysis)
    bgm = planned["bgm"]
    reasons = " ".join(str(seg.get("reason", "")) for seg in bgm.get("segments", []))
    assert "playing / fooling around" in reasons
    play_spans = [
        float(seg["end_sec"]) - float(seg["start_sec"])
        for seg in bgm["segments"]
        if "playing / fooling around" in str(seg.get("reason", ""))
    ]
    assert play_spans
    assert max(play_spans) <= 40.0 + 1e-6
    coverage = sum(
        float(seg["end_sec"]) - float(seg["start_sec"]) for seg in bgm["segments"]
    )
    assert coverage <= float(planned["duration_sec"]) * 0.65 + 1e-6
    files = [str(seg.get("file", "")) for seg in bgm["segments"]]
    assert all(files)
    if len(files) >= 2 and len({Path(f).name for f in files}) >= 2:
        # Consecutive beds should vary when multiple pack candidates exist.
        assert files[0] != files[1]


def test_render_graph_includes_sfx_adelay_and_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = Episode(tmp_path, copy.deepcopy(DEFAULT_CONFIG))
    for name in ("footage", "work", "output", "audio"):
        (tmp_path / name).mkdir()
    (episode.footage / "clip.mp4").write_bytes(b"fake")
    sfx = tmp_path / "audio" / "sfx" / "pop" / "pop_01.wav"
    sfx.parent.mkdir(parents=True)
    sfx.write_bytes(b"fake")
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
        "audio_cues": [
            {
                "at_sec": 0.5,
                "type": "pop",
                "file": "audio/sfx/pop/pop_01.wav",
                "gain": 0.5,
                "license": "cc0",
                "reason": "punch",
            }
        ],
    }
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    _, filters, _ = build_render_command(episode, plan, episode.output / "final.mp4")
    assert "adelay=500|500" in filters
    assert "[acat][sfxmix]amix=inputs=2" in filters
