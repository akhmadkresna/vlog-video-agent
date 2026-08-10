from __future__ import annotations

from pathlib import Path

from vlog_editor.dashboard import render_clip_card_html
from vlog_editor.kids_interest import (
    best_wow_span,
    parse_frame_timestamp,
    score_window,
    wow_moments,
)
from vlog_editor.planner import select_excerpt
from vlog_editor.project import DEFAULT_CONFIG, Episode


def test_score_window_penalizes_setup_vs_payoff() -> None:
    setup = score_window(
        "children walk",
        visual_reason="Children walk toward the fish enclosure, establishing context.",
    )
    payoff = score_window(
        "Wow ini besar banget ikan",
        visual_reason="Close-up of a large fish in water, highlighting its size.",
        shot_type="close",
    )
    assert setup["phase"] == "setup"
    assert payoff["phase"] == "payoff"
    assert payoff["score"] > setup["score"]
    assert payoff["score"] >= 0.45
    assert "animals" in payoff["stimulus"] or "novelty" in payoff["stimulus"]
    assert "wonder" in payoff["affect"]


def test_wow_moments_fish_style_peaks_after_walkup() -> None:
    source = {
        "metadata": {"duration": 150.0, "filename": "fish.mp4"},
        "audio": {
            "segments": [
                {"start": 18.0, "end": 28.8, "text": "Oh iya di sini kayaknya besar-besar ya"},
                {
                    "start": 28.8,
                    "end": 43.3,
                    "text": "besar banget ikan kira-kira itu kura-kuranya ikan ikan",
                },
                {
                    "start": 58.7,
                    "end": 69.9,
                    "text": "ini loh disini ikannya besar banget tuh ikan",
                },
            ],
            "words": [],
            "text": "besar banget ikan",
        },
        "visual": {
            "summary": "Two children observe large fish in an outdoor zoo enclosure",
            "subjects": ["children", "fish"],
            "shot_type": "mixed",
            "recommended_ranges": [
                {
                    "start": 0.0,
                    "end": 8.0,
                    "reason": "Children walk toward the fish enclosure, establishing context and movement.",
                },
                {
                    "start": 15.0,
                    "end": 23.0,
                    "reason": "Close-up of a large fish in water, highlighting its size and texture.",
                },
                {
                    "start": 48.0,
                    "end": 60.0,
                    "reason": "Children stand at the railing, observing the fish with interest.",
                },
            ],
        },
    }
    moments = wow_moments(source)
    assert moments
    best = max(moments, key=lambda item: float(item["score"]))
    assert best["t0"] >= 15.0
    assert float(best["score"]) >= 0.45

    picked = best_wow_span(source, 36.0)
    assert picked is not None
    start, end, moment = picked
    assert start >= 14.0  # tiny pre-roll before payoff anchor\n    assert start < 30.0\n    assert end > start\n    assert float(moment["score"]) >= 0.45


def test_select_excerpt_prefers_fish_payoff_over_walkup() -> None:
    source = {
        "metadata": {"duration": 150.0, "filename": "fish.mp4"},
        "audio": {
            "segments": [
                {"start": 0.5, "end": 8.0, "text": "ayo kita ke sana pelan-pelan"},
                {
                    "start": 28.8,
                    "end": 43.3,
                    "text": "besar banget ikan wow lihat ya",
                },
                {
                    "start": 48.0,
                    "end": 60.0,
                    "text": "ikannya besar banget tuh anak-anak lihat",
                },
            ],
            "words": [],
            "text": "besar banget ikan",
        },
        "visual": {
            "summary": "Two children observe large fish",
            "subjects": ["children", "fish"],
            "shot_type": "mixed",
            "recommended_ranges": [
                {
                    "start": 0.0,
                    "end": 8.0,
                    "reason": "Children walk toward the fish enclosure, establishing context.",
                },
                {
                    "start": 15.0,
                    "end": 23.0,
                    "reason": "Close-up of a large fish in water.",
                },
                {
                    "start": 48.0,
                    "end": 60.0,
                    "reason": "Children stand at the railing, observing the fish with interest.",
                },
            ],
        },
    }
    start, end = select_excerpt(source, 36.0)
    assert start >= 15.0
    assert end - start >= 10.0


def test_parse_frame_timestamp() -> None:
    assert parse_frame_timestamp("work/frames/0003/frame_004_48.250.jpg") == 48.25
    assert parse_frame_timestamp("frame_001_0.500.jpg") == 0.5
    assert parse_frame_timestamp("thumb_abc.jpg") is None


def test_dashboard_card_includes_narration_and_wow(tmp_path: Path) -> None:
    footage = tmp_path / "footage"
    work = tmp_path / "work"
    frames = work / "frames" / "0001"
    footage.mkdir()
    frames.mkdir(parents=True)
    dashboard = work / "dashboard"
    dashboard.mkdir(parents=True)
    assets = dashboard / "assets"
    assets.mkdir()

    frame_a = frames / "frame_001_20.000.jpg"
    frame_b = frames / "frame_002_50.000.jpg"
    frame_a.write_bytes(b"fakejpg")
    frame_b.write_bytes(b"fakejpg")

    source_clip = {
        "metadata": {"filename": "clip.mp4", "duration": 80.0},
        "frames": [
            str(frame_a.relative_to(tmp_path)),
            str(frame_b.relative_to(tmp_path)),
        ],
        "audio": {
            "segments": [
                {"start": 18.0, "end": 24.0, "text": "ayo jalan dulu"},
                {"start": 48.0, "end": 55.0, "text": "wow besar banget ikan lihat"},
            ],
            "words": [],
        },
        "visual": {
            "summary": "Children watch fish",
            "subjects": ["children", "fish"],
            "shot_type": "close",
            "recommended_ranges": [
                {
                    "start": 48.0,
                    "end": 56.0,
                    "reason": "Close-up of a large fish with children reacting.",
                }
            ],
        },
    }
    episode = Episode(tmp_path, dict(DEFAULT_CONFIG))
    used: set[Path] = set()
    html_out = render_clip_card_html(
        episode,
        {
            "file": "clip.mp4",
            "start": 15.0,
            "end": 60.0,
            "note": "Fish beat",
            "subtitle": "wow besar banget ikan",
            "wow": {
                "score": 0.8,
                "phase": "payoff",
                "stimulus": ["animals"],
                "affect": ["wonder"],
                "reasons": ["stimulus:animals", "affect:wonder"],
            },
        },
        timeline_start=0.0,
        timeline_end=45.0,
        duration=45.0,
        thumb_relative="assets/thumb_x.jpg",
        source_clip=source_clip,
        dashboard=dashboard,
        used_assets=used,
    )
    assert "Wow 0.80" in html_out
    assert "data-wow-score=" in html_out
    assert "data-frame-review=" in html_out
    assert "wow besar banget ikan" in html_out
    assert "frame-narration" in html_out
    assert "frame-cell" in html_out

