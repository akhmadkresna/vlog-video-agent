# YouTube upload

`ve upload` sends `output/final.mp4` plus the sidecar `output/captions.srt` to YouTube
Data API v3. Title, description, tags, privacy, and the COPPA **made for kids** flag
come from `work/youtube_listing.json`.

OAuth files stay on this PC. Never commit them.

## One-time Google Cloud setup

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create (or pick) a project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen (External is fine). Add your YouTube Google account as a test user.
4. Create credentials → OAuth client ID → application type **Desktop app**.
5. Download the JSON to:

```text
%USERPROFILE%\.vlog-editor\youtube-client-secrets.json
```

6. Login once (opens a browser):

```powershell
cd D:\AI\vlog-editor-agent
uv run ve youtube-login
```

That writes `%USERPROFILE%\.vlog-editor\youtube-token.json`.

Optional env overrides: `VLOG_YOUTUBE_DIR`, `VLOG_YOUTUBE_CLIENT_SECRETS`, `VLOG_YOUTUBE_TOKEN`.

## Per episode

```powershell
uv run ve youtube-meta D:\Videos\dji-15072026\ep01-school-mornings
# Review/edit work\youtube_listing.json
uv run ve upload D:\Videos\dji-15072026\ep01-school-mornings
```

`--dry-run` writes `work/youtube_dry_run.json` without calling YouTube.

```powershell
uv run ve upload D:\Videos\dji-15072026\ep01-school-mornings --privacy unlisted --made-for-kids
uv run ve upload D:\Videos\dji-15072026\ep01-school-mornings --not-made-for-kids --privacy private
```

Public uploads need an explicit `--privacy public` (or `youtube.privacy: public` in
`project.yaml`) after the user asked for public.

## Listing fields

| Field | Default | Notes |
|---|---|---|
| `title` | episode `title` | Max 100 characters |
| `description` | generated | Chapters from the edit plan + Indo hashtags |
| `tags` | generated | No personal names from ASR |
| `privacy` | `unlisted` | `private`, `unlisted`, or `public` |
| `made_for_kids` | `true` | YouTube `selfDeclaredMadeForKids` |
| `upload_captions` | `true` | Uploads `output/captions.srt` as Indonesian |
| `category_id` | `22` | People & Blogs |
| `notify_subscribers` | `false` | |

Override per episode in `project.yaml`:

```yaml
youtube:
  privacy: unlisted
  made_for_kids: true
  tags: [sekolah, jemput]
  playlist_id: null
```

A successful upload writes `output/youtube_upload.json` with the video id and Studio URL.
Use `--force` only to upload a second copy.
