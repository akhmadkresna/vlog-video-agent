"""YouTube listing, OAuth, resumable upload, and sidecar caption upload."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vlog_editor.project import Episode, read_json, write_json

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_SCOPES = (YOUTUBE_UPLOAD_SCOPE, YOUTUBE_FORCE_SSL_SCOPE)

DEFAULT_YOUTUBE: dict[str, Any] = {
    # Unlisted until the user asks to publish. YouTube Kids only indexes public MFK videos.
    "privacy": "unlisted",
    # Entertainment, not People & Blogs (22), which reads as a parent vlog.
    "category_id": "24",
    "made_for_kids": True,
    "kids_destination": True,
    "notify_subscribers": False,
    "upload_captions": True,
    "title": None,
    "description": None,
    "tags": [],
    "playlist_id": None,
    "contains_synthetic_media": False,
    "paid_promotion": False,
    "embeddable": True,
}

_URL_RE = re.compile(r"https?://|www\.|\bbit\.ly\b|\bt\.co\b", re.IGNORECASE)
_KIDS_TAG_BLOCKLIST = {
    "cta",
    "close",
    "greeting",
    "subscribe",
    "merch",
    "buy",
    "sale",
    "promo",
    "discord",
    "instagram",
    "tiktok",
    "whatsapp",
}
_KIDS_SECTION_RULES: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = (
    (re.compile(r"greeting\s*open|^\s*halo\s*$|^\s*hello\s*$", re.IGNORECASE), {"id": "Halo", "en": "Hello"}),
    (re.compile(r"cta\s*close|^\s*dadah\s*$|bye-bye|goodbye", re.IGNORECASE), {"id": "Dadah", "en": "Bye-bye"}),
    (re.compile(r"game|speedstorm|gameplay", re.IGNORECASE), {"id": "Main game", "en": "Game time"}),
    (re.compile(r"vehicle|car|mobil", re.IGNORECASE), {"id": "Di mobil", "en": "In the car"}),
    (re.compile(r"outdoor|luar|outside", re.IGNORECASE), {"id": "Main di luar", "en": "Outside"}),
    (re.compile(r"home|rumah", re.IGNORECASE), {"id": "Di rumah", "en": "At home"}),
    (re.compile(r"school|sekolah|pickup|jemput", re.IGNORECASE), {"id": "Sekolah", "en": "School"}),
)

_PRIVACY = {"private", "unlisted", "public"}
_STOPWORDS = {
    "a",
    "and",
    "best",
    "coverage",
    "in",
    "keep",
    "moments",
    "of",
    "open",
    "the",
    "to",
    "with",
    "yang",
    "dan",
    "di",
}
_LANGUAGE_NAMES = {"id": "Indonesian", "en": "English"}


def listing_path(episode: Episode) -> Path:
    return episode.work / "youtube_listing.json"


def upload_record_path(episode: Episode) -> Path:
    return episode.output / "youtube_upload.json"


def credentials_dir() -> Path:
    override = os.environ.get("VLOG_YOUTUBE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".vlog-editor"


def client_secrets_path() -> Path:
    env = os.environ.get("VLOG_YOUTUBE_CLIENT_SECRETS")
    if env:
        return Path(env).expanduser()
    return credentials_dir() / "youtube-client-secrets.json"


def token_path() -> Path:
    env = os.environ.get("VLOG_YOUTUBE_TOKEN")
    if env:
        return Path(env).expanduser()
    return credentials_dir() / "youtube-token.json"


def youtube_config(episode: Episode) -> dict[str, Any]:
    merged = dict(DEFAULT_YOUTUBE)
    raw = episode.config.get("youtube")
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def episode_language(episode: Episode) -> str:
    language = str(episode.config.get("language") or "id").strip().lower()
    if language in {"", "auto"}:
        return "id"
    return language


def _require_google() -> dict[str, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "YouTube libraries missing. From D:\\AI\\vlog-editor-agent run `uv sync`."
        ) from exc
    return {
        "Request": Request,
        "Credentials": Credentials,
        "InstalledAppFlow": InstalledAppFlow,
        "build": build,
        "HttpError": HttpError,
        "MediaFileUpload": MediaFileUpload,
    }


def youtube_ready() -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []
    try:
        _require_google()
        ok.append("YouTube libs: google-api-python-client import succeeded")
    except RuntimeError as exc:
        problems.append(str(exc))
        return ok, problems
    secrets = client_secrets_path()
    if secrets.is_file():
        ok.append(f"YouTube OAuth client: {secrets}")
    else:
        problems.append(
            f"YouTube OAuth client missing: copy Desktop client JSON to {secrets}"
        )
    token = token_path()
    if token.is_file():
        ok.append(f"YouTube login token: {token}")
    else:
        problems.append("YouTube login: run `ve youtube-login` once in a browser")
    return ok, problems


def _load_credentials(google: dict[str, Any]) -> Any:
    secrets = client_secrets_path()
    token = token_path()
    credentials = None
    if token.is_file():
        credentials = google["Credentials"].from_authorized_user_file(str(token), YOUTUBE_SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(google["Request"]())
    if credentials and credentials.valid:
        return credentials
    if not secrets.is_file():
        raise FileNotFoundError(
            "Missing YouTube OAuth client. Create a Desktop app client in Google Cloud, "
            f"enable YouTube Data API v3, and save the JSON to {secrets}. "
            "See docs/youtube-upload.md."
        )
    flow = google["InstalledAppFlow"].from_client_secrets_file(str(secrets), list(YOUTUBE_SCOPES))
    credentials = flow.run_local_server(port=0, prompt="consent")
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def login() -> Path:
    google = _require_google()
    credentials = _load_credentials(google)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")
    return path


def build_youtube_client(google: dict[str, Any] | None = None) -> Any:
    google = google or _require_google()
    credentials = _load_credentials(google)
    return google["build"]("youtube", "v3", credentials=credentials, cache_discovery=False)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def kids_section_label(label: str, language: str = "id") -> str:
    text = str(label or "").strip() or "Chapter"
    lang = "id" if language == "id" else "en"
    for pattern, names in _KIDS_SECTION_RULES:
        if pattern.search(text):
            return names[lang]
    cleaned = re.sub(r"\s*[—-]\s*.*$", "", text).strip()
    return cleaned or names_fallback(lang)


def names_fallback(language: str) -> str:
    return "Main" if language == "id" else "Play"


def chapter_timestamps(
    plan: dict[str, Any],
    *,
    language: str = "id",
    kids_destination: bool = True,
) -> list[tuple[str, str]]:
    cursor = 0.0
    chapters: list[tuple[str, str]] = []
    for section in plan.get("structure", []) or []:
        if not isinstance(section, dict):
            continue
        clips = [clip for clip in section.get("clips", []) or [] if isinstance(clip, dict)]
        if not clips:
            continue
        label = str(section.get("section") or "Chapter").strip() or "Chapter"
        if kids_destination:
            label = kids_section_label(label, language)
        chapters.append((format_timestamp(cursor), label))
        for clip in clips:
            cursor += max(0.0, float(clip.get("end", 0)) - float(clip.get("start", 0)))
    if chapters and chapters[0][0] != "0:00":
        chapters[0] = ("0:00", chapters[0][1])
    return chapters


def _slug_tokens(*parts: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for raw in re.split(r"[^\w]+", part.lower(), flags=re.UNICODE):
            token = raw.strip()
            if len(token) < 3 or token in _STOPWORDS or token.isdigit() or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def build_tags(episode: Episode, plan: dict[str, Any], extra: list[str] | None = None) -> list[str]:
    config = youtube_config(episode)
    language = episode_language(episode)
    kids = bool(config.get("kids_destination", True))
    seeds = [
        str(episode.config.get("title") or ""),
        "anak" if language == "id" else "kids",
        "keluarga" if language == "id" else "family",
        "indonesia" if language == "id" else language,
        "bermain" if language == "id" else "play",
    ]
    if not kids:
        seeds.extend(["vlog", str(episode.config.get("style") or "")])
    for section in plan.get("structure", []) or []:
        if isinstance(section, dict):
            label = str(section.get("section") or "")
            seeds.append(kids_section_label(label, language) if kids else label)
    seeds.extend(str(item) for item in (extra or []))
    seeds.extend(str(item) for item in config.get("tags") or [])
    tags: list[str] = []
    used = 0
    for token in _slug_tokens(*seeds):
        if token in _KIDS_TAG_BLOCKLIST:
            continue
        if used + len(token) + 1 > 450 or len(tags) >= 15:
            break
        tags.append(token)
        used += len(token) + 1
    return tags


def build_description(
    episode: Episode,
    plan: dict[str, Any],
    *,
    title: str,
    tags: list[str],
) -> str:
    config = youtube_config(episode)
    kids = bool(config.get("kids_destination", True))
    if config.get("description"):
        text = str(config["description"])[:5000]
        if kids and _URL_RE.search(text):
            raise ValueError("YouTube Kids listings cannot include URLs in the description.")
        return text
    language = episode_language(episode)
    chapters = chapter_timestamps(plan, language=language, kids_destination=kids)
    if kids and language == "id":
        lines = [
            title,
            "",
            "Video untuk anak: main, jalan-jalan, dan momen seru bersama keluarga.",
            "Bahasa Indonesia. Cocok ditonton bareng.",
            "",
        ]
        heading = "Isi video:"
        hashtags = ["#anak", "#keluarga", "#indonesia"]
    elif kids:
        lines = [
            title,
            "",
            "A kids video: play, going out, and fun family moments.",
            "Simple language. Good to watch together.",
            "",
        ]
        heading = "In this video:"
        hashtags = ["#kids", "#family", "#indonesia"]
    elif language == "id":
        style = str(episode.config.get("style") or "").strip()
        lines = [
            title,
            "",
            f"Vlog keluarga: {style}.".strip(),
            "Rekaman asli, dipotong lokal. Caption Indonesia ada di file SRT.",
            "",
        ]
        heading = "Babak:"
        hashtags = ["#vlogkeluarga", "#anak", "#indonesia"]
    else:
        lines = [
            title,
            "",
            "Family vlog. Edited locally. Captions are a separate SRT.",
            "",
        ]
        heading = "Chapters:"
        hashtags = ["#familyvlog", "#kids", "#indonesia"]
    if chapters:
        lines.append(heading)
        lines.extend(f"{stamp} {label}" for stamp, label in chapters)
        lines.append("")
    if not kids:
        for tag in tags[:8]:
            hash_tag = "#" + re.sub(r"[^\w]", "", tag, flags=re.UNICODE)
            if len(hash_tag) > 2 and hash_tag.lower() not in {item.lower() for item in hashtags}:
                hashtags.append(hash_tag)
    lines.append(" ".join(hashtags[:5]))
    description = "\n".join(lines).strip()[:5000]
    if kids and _URL_RE.search(description):
        raise ValueError("YouTube Kids listings cannot include URLs in the description.")
    return description


def generate_listing(
    episode: Episode,
    *,
    privacy: str | None = None,
    made_for_kids: bool | None = None,
) -> dict[str, Any]:
    config = youtube_config(episode)
    plan = read_json(episode.plan_path) if episode.plan_path.is_file() else {"structure": []}
    title = str(config.get("title") or episode.config.get("title") or "Vlog").strip()[:100]
    tags = build_tags(episode, plan)
    privacy_value = str(privacy or config.get("privacy") or "unlisted").strip().lower()
    if privacy_value not in _PRIVACY:
        raise ValueError(f"Invalid YouTube privacy {privacy_value!r}; use private, unlisted, or public")
    kids_destination = bool(config.get("kids_destination", True))
    if made_for_kids is None:
        kids_flag = True if kids_destination else bool(config.get("made_for_kids", True))
    else:
        kids_flag = bool(made_for_kids)
        if kids_destination and not kids_flag:
            raise ValueError(
                "kids_destination requires made_for_kids=true (YouTube Kids / COPPA)."
            )
    listing = {
        "title": title,
        "description": build_description(episode, plan, title=title, tags=tags),
        "tags": tags,
        "category_id": str(config.get("category_id") or "24"),
        "privacy": privacy_value,
        "made_for_kids": kids_flag,
        "kids_destination": kids_destination,
        "notify_subscribers": False if kids_destination else bool(config.get("notify_subscribers", False)),
        "upload_captions": bool(config.get("upload_captions", True)),
        "language": episode_language(episode),
        "playlist_id": config.get("playlist_id") or None,
        "contains_synthetic_media": bool(config.get("contains_synthetic_media", False)),
        "paid_promotion": False if kids_destination else bool(config.get("paid_promotion", False)),
        "embeddable": bool(config.get("embeddable", True)),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    write_json(listing_path(episode), listing)
    return listing


def load_listing(episode: Episode) -> dict[str, Any]:
    path = listing_path(episode)
    if not path.is_file():
        return generate_listing(episode)
    listing = read_json(path)
    if not isinstance(listing, dict):
        raise TypeError("work/youtube_listing.json must contain an object")
    return listing


def video_body(listing: dict[str, Any]) -> dict[str, Any]:
    language = str(listing.get("language") or "id")
    status: dict[str, Any] = {
        "privacyStatus": str(listing.get("privacy") or "unlisted"),
        "selfDeclaredMadeForKids": bool(listing.get("made_for_kids", True)),
        "embeddable": bool(listing.get("embeddable", True)),
        "publicStatsViewable": True,
        "license": "youtube",
    }
    if listing.get("contains_synthetic_media"):
        status["containsSyntheticMedia"] = True
    snippet: dict[str, Any] = {
        "title": str(listing.get("title") or "Vlog")[:100],
        "description": str(listing.get("description") or "")[:5000],
        "tags": list(listing.get("tags") or []),
        "categoryId": str(listing.get("category_id") or "24"),
        "defaultLanguage": language,
        "defaultAudioLanguage": language,
    }
    return {"snippet": snippet, "status": status}


def _rendered_video(episode: Episode) -> Path:
    path = episode.output / "final.mp4"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}. Run `ve render {episode.root}` first.")
    return path


def _caption_file(episode: Episode) -> Path | None:
    path = episode.output / "captions.srt"
    return path if path.is_file() else None


def _resumable_execute(request: Any, *, label: str) -> dict[str, Any]:
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            percent = int(status.progress() * 100)
            print(f"{label} {percent}%")
    if not isinstance(response, dict) or not response.get("id"):
        raise RuntimeError(f"{label} failed: {response!r}")
    return response


def _insert_captions(
    youtube: Any,
    google: dict[str, Any],
    *,
    video_id: str,
    srt_path: Path,
    language: str,
) -> dict[str, Any]:
    media = google["MediaFileUpload"](
        str(srt_path),
        mimetype="application/octet-stream",
        resumable=True,
    )
    request = youtube.captions().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "language": language,
                "name": _LANGUAGE_NAMES.get(language, language),
                "isDraft": False,
            }
        },
        media_body=media,
    )
    return _resumable_execute(request, label="Captions")


def _add_to_playlist(youtube: Any, *, video_id: str, playlist_id: str) -> None:
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def apply_listing_overrides(
    listing: dict[str, Any],
    *,
    privacy: str | None = None,
    made_for_kids: bool | None = None,
) -> dict[str, Any]:
    updated = dict(listing)
    if privacy is not None:
        value = str(privacy).strip().lower()
        if value not in _PRIVACY:
            raise ValueError(f"Invalid YouTube privacy {value!r}; use private, unlisted, or public")
        updated["privacy"] = value
    if made_for_kids is not None:
        updated["made_for_kids"] = bool(made_for_kids)
    return updated


def upload_episode(
    episode: Episode,
    *,
    privacy: str | None = None,
    made_for_kids: bool | None = None,
    skip_captions: bool = False,
    force: bool = False,
    dry_run: bool = False,
    youtube: Any | None = None,
    google: dict[str, Any] | None = None,
) -> dict[str, Any]:
    listing = apply_listing_overrides(
        load_listing(episode),
        privacy=privacy,
        made_for_kids=made_for_kids,
    )
    write_json(listing_path(episode), listing)
    record_path = upload_record_path(episode)
    if record_path.is_file() and not force and not dry_run:
        existing = read_json(record_path)
        video_id = str(existing.get("video_id") or "")
        if video_id:
            raise FileExistsError(
                f"Already uploaded as https://youtu.be/{video_id}. "
                "Pass --force to upload a new copy."
            )
    video_path = _rendered_video(episode)
    body = video_body(listing)
    if dry_run:
        payload = {
            "dry_run": True,
            "file": str(video_path),
            "captions": None if skip_captions else str(_caption_file(episode) or ""),
            "listing": listing,
            "request": body,
        }
        write_json(episode.work / "youtube_dry_run.json", payload)
        return payload

    google = google or _require_google()
    youtube = youtube or build_youtube_client(google)
    media = google["MediaFileUpload"](
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=bool(listing.get("notify_subscribers", False)),
    )
    print(f"Uploading {video_path.name} ({video_path.stat().st_size} bytes)...")
    response = _resumable_execute(request, label="Video")
    video_id = str(response["id"])
    caption_id = None
    srt = None if skip_captions or not listing.get("upload_captions", True) else _caption_file(episode)
    if srt is not None:
        try:
            caption = _insert_captions(
                youtube,
                google,
                video_id=video_id,
                srt_path=srt,
                language=str(listing.get("language") or "id"),
            )
            caption_id = caption.get("id")
        except google["HttpError"] as exc:
            print(f"WARN captions upload failed: {exc}")
    playlist_id = listing.get("playlist_id")
    if playlist_id:
        _add_to_playlist(youtube, video_id=video_id, playlist_id=str(playlist_id))
    record = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "privacy": listing["privacy"],
        "made_for_kids": listing["made_for_kids"],
        "caption_id": caption_id,
        "title": listing["title"],
        "uploaded_at": datetime.now(UTC).isoformat(),
        "file": str(video_path),
    }
    write_json(record_path, record)
    return record
