# YouTube Kids configuration

This framework is for **YouTube Kids** family videos (real kids playing, school
runs, games). `made_for_kids: true` is required, but it is only the COPPA audience
flag. The Kids **app** is a second, stricter filter.

## Two layers

| Layer | What it does | How we set it |
|---|---|---|
| Made for Kids (COPPA) | Disables comments, notifications, cards, end screens, personalized ads | `selfDeclaredMadeForKids: true` on every upload |
| YouTube Kids app | Age-appropriate subset of public MFK videos | YouTube decides. We can only make videos *eligible* |

YouTube’s own example of a **parent** family vlog (“telling other parents about a
park visit”) is **not** made-for-kids. Metadata must read as **for children**, not
for parents.

Family vlogs are listed under the Kids app **Younger** band (ages 5–8), not Preschool.

## Framework defaults (locked)

| Config | Value | Why |
|---|---|---|
| `youtube.made_for_kids` | `true` | COPPA. Do not override. |
| `youtube.kids_destination` | `true` | Blocks `--not-made-for-kids`, URLs, paid promo |
| `youtube.category_id` | `24` Entertainment | `22` People & Blogs looks like a parent vlog |
| `youtube.notify_subscribers` | `false` | Notifications are off on MFK anyway |
| `youtube.upload_captions` | `true` | Soft `captions.srt`, not burn-in |
| `youtube.paid_promotion` | `false` | Paid placement is **removed from YouTube Kids** |
| `youtube.contains_synthetic_media` | `false` | Real camera footage |
| `youtube.privacy` | `unlisted` | Review first. **Kids app only sees public** |
| Listing copy | Child-facing Indo | No “CTA close”, no subscribe, no links |
| Tags | Short, no jargon | No `cta` / merch / social URLs |
| Hashtags | Max a few | Kids policy bans keyword stuffing |

## Must set in YouTube Studio (API cannot)

1. **Channel audience** → entire channel “made for kids” if this channel is kids-only.
2. After review, set the video **Public** if you want it in the Kids app.
3. Custom **thumbnail**: happy play, no scare/clickbait, no school name, address, or surname on the image.
4. Do **not** add cards, end screens, merch, donate, or memberships (disabled on MFK; also fail Kids policy if forced).
5. Do **not** disclose paid product placement on these videos.

## Do not put in title / description / tags / on-camera

- Home address, school name, classroom number, daily route
- Children’s surnames or full legal names (nicknames already in speech are enough)
- External links (IG, TikTok, WhatsApp, shops)
- “Like and subscribe”, merch, giveaways
- Adult arguments, profanity, scary pranks, jump-scare SFX

## Still missing (not built yet)

- Kids-safe custom thumbnail generator + `thumbnails.set`
- ASR profanity / adult-conflict gate before approve
- Auto-public after a human “publish to Kids” confirm
- Channel-level audience API
- Location metadata (keep **off** — never attach home GPS)

## Publish flow

```powershell
uv run ve youtube-meta <episode>          # kids listing
# review work\youtube_listing.json
uv run ve upload <episode>                # unlisted + MFK + SRT
# watch on phone, then in Studio set Public for Kids app
```
