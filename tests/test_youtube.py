from __future__ import annotations

import copy
from argparse import Namespace
from pathlib import Path

from vlog_editor.cli import dispatch
from vlog_editor.project import DEFAULT_CONFIG, Episode, write_json
from vlog_editor.youtube import (
    apply_listing_overrides,
    build_tags,
    chapter_timestamps,
    generate_listing,
    listing_path,
    upload_episode,
    video_body,
)


def _episode(tmp_path: Path) -> Episode:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["title"] = "School mornings and pickups"
    config["language"] = "id"
    config["style"] = "warm candid Indo family kids vlog"
    return Episode(tmp_path, config)


def _plan() -> dict:
    return {
        "title": "School mornings and pickups",
        "structure": [
            {
                "section": "Greeting open",
                "clips": [{"file": "a.mp4", "start": 0.0, "end": 16.0}],
            },
            {
                "section": "Outdoor — Best moments",
                "clips": [{"file": "b.mp4", "start": 2.0, "end": 22.0}],
            },
        ],
    }


def test_chapter_timestamps_start_at_zero() -> None:
    chapters = chapter_timestamps(_plan())
    assert chapters[0] == ("0:00", "Halo")
    assert chapters[1][0] == "0:16"
    assert chapters[1][1] == "Main di luar"


def test_generate_listing_sets_kids_audience_and_indonesian_copy(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    listing = generate_listing(episode)
    assert listing["made_for_kids"] is True
    assert listing["kids_destination"] is True
    assert listing["privacy"] == "unlisted"
    assert listing["language"] == "id"
    assert listing["category_id"] == "24"
    assert listing["paid_promotion"] is False
    assert listing["title"] == "School mornings and pickups"
    assert "0:00 Halo" in listing["description"]
    assert "0:16 Main di luar" in listing["description"]
    assert "Video untuk anak" in listing["description"]
    assert "http" not in listing["description"].lower()
    assert listing["description"].count("#") <= 5
    assert "cta" not in listing["tags"]
    assert "anak" in listing["tags"]
    assert "keluarga" in listing["tags"]
    assert listing_path(episode).is_file()


def test_not_made_for_kids_override(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    episode.config["youtube"]["kids_destination"] = False
    write_json(episode.plan_path, _plan())
    listing = generate_listing(episode, made_for_kids=False, privacy="private")
    assert listing["made_for_kids"] is False
    assert listing["privacy"] == "private"


def test_kids_destination_rejects_not_made_for_kids(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    try:
        generate_listing(episode, made_for_kids=False)
    except ValueError as exc:
        assert "made_for_kids" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_video_body_declares_made_for_kids() -> None:
    listing = {
        "title": "Weekend at home",
        "description": "desc",
        "tags": ["vlog", "anak"],
        "category_id": "22",
        "privacy": "unlisted",
        "made_for_kids": True,
        "language": "id",
        "contains_synthetic_media": False,
    }
    body = video_body(listing)
    assert body["status"]["selfDeclaredMadeForKids"] is True
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["snippet"]["defaultLanguage"] == "id"
    assert body["snippet"]["tags"] == ["vlog", "anak"]


def test_upload_dry_run_writes_request_without_api(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    (episode.output / "final.mp4").write_bytes(b"fake-video")
    (episode.output / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHai\n", encoding="utf-8")
    result = upload_episode(episode, dry_run=True)
    assert result["dry_run"] is True
    assert result["request"]["status"]["selfDeclaredMadeForKids"] is True
    assert "final.mp4" in result["file"]
    assert result["captions"].endswith("captions.srt")


def test_upload_uses_existing_listing_edits(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    (episode.output / "final.mp4").write_bytes(b"fake-video")
    generate_listing(episode)
    write_json(
        listing_path(episode),
        apply_listing_overrides(
            {
                **generate_listing(episode),
                "title": "Custom title",
                "description": "Edited description",
                "tags": ["customtag"],
            },
            made_for_kids=True,
        ),
    )
    result = upload_episode(episode, dry_run=True)
    assert result["listing"]["title"] == "Custom title"
    assert result["listing"]["description"] == "Edited description"
    assert result["listing"]["tags"] == ["customtag"]


class _FakeStatus:
    def __init__(self, ratio: float) -> None:
        self._ratio = ratio

    def progress(self) -> float:
        return self._ratio


class _FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        if self.calls == 1:
            return _FakeStatus(0.5), None
        return None, self.payload


class _FakeInsert:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def insert(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self.payload)


class _FakeYouTube:
    def __init__(self) -> None:
        self.videos_api = _FakeInsert({"id": "abc123"})
        self.captions_api = _FakeInsert({"id": "cap1"})

    def videos(self) -> _FakeInsert:
        return self.videos_api

    def captions(self) -> _FakeInsert:
        return self.captions_api


def test_upload_episode_posts_kids_flag_and_captions(tmp_path: Path, monkeypatch) -> None:
    episode = _episode(tmp_path)
    write_json(episode.plan_path, _plan())
    (episode.output / "final.mp4").write_bytes(b"fake-video")
    (episode.output / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHai\n", encoding="utf-8")
    fake = _FakeYouTube()
    monkeypatch.setattr(
        "vlog_editor.youtube.MediaFileUpload",
        lambda *args, **kwargs: "media",
        raising=False,
    )

    class DummyHttpError(Exception):
        pass

    google = {
        "MediaFileUpload": lambda *args, **kwargs: "media",
        "HttpError": DummyHttpError,
    }
    record = upload_episode(episode, youtube=fake, google=google)
    video_kwargs = fake.videos_api.calls[0]
    assert video_kwargs["body"]["status"]["selfDeclaredMadeForKids"] is True
    assert video_kwargs["notifySubscribers"] is False
    caption_kwargs = fake.captions_api.calls[0]
    assert caption_kwargs["body"]["snippet"]["videoId"] == "abc123"
    assert caption_kwargs["body"]["snippet"]["language"] == "id"
    assert record["url"] == "https://youtu.be/abc123"
    assert record["caption_id"] == "cap1"


def test_cli_youtube_meta_and_upload_dry_run(tmp_path: Path, capsys) -> None:
    episode = _episode(tmp_path)
    (episode.root / "project.yaml").write_text(
        "title: School mornings and pickups\nlanguage: id\n",
        encoding="utf-8",
    )
    write_json(episode.plan_path, _plan())
    (episode.output / "final.mp4").write_bytes(b"fake-video")
    result = dispatch(Namespace(command="youtube-meta", episode=str(episode.root), privacy=None, made_for_kids=None))
    assert result == 0
    out = capsys.readouterr().out
    assert "Made for kids: True" in out
    result = dispatch(
        Namespace(
            command="upload",
            episode=str(episode.root),
            dry_run=True,
            privacy="unlisted",
            made_for_kids=True,
            skip_captions=False,
            force=False,
        )
    )
    assert result == 0
    assert "Dry run" in capsys.readouterr().out


def test_build_tags_caps_length(tmp_path: Path) -> None:
    episode = _episode(tmp_path)
    tags = build_tags(episode, _plan(), extra=["x" * 80 for _ in range(20)])
    assert sum(len(tag) + 1 for tag in tags) <= 451
    assert len(tags) <= 25
