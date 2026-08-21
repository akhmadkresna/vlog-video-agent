# Default audio sources

Nothing here was ripped from YouTube channels by the tooling.

## Style reference (not a source of audio)

Public Indonesian kids/family channels such as **Leika Garudita** use energetic
family-comedy punctuation: short whooshes/clicks on cut changes, pops/boings on
reactions, soft chimes or goofy laughs on cute moments, and light fail/success
tones. Editing is often CapCut-adjacent. We map that *style* onto local pack files.

Do **not** extract audio from Leika (or any) videos — those tracks are copyrighted.

## Libraries used

| Library | License | Pack / pages |
| --- | --- | --- |
| [BigSoundBank](https://bigsoundbank.com/) (Joseph Sardin) | CC0 | Cartoon boings |
| [Kenney Interface Sounds](https://kenney.nl/assets/interface-sounds) | CC0 | Plucks, glass, confirmation |
| [Pixabay](https://pixabay.com/) music | Pixabay Content License | Kids BGM beds (rotated per bed) |
| Local `sfx/meme/` drops | `user_provided` | Pack-owner meme files (not CC0) |
| [BigSoundBank](https://bigsoundbank.com/) (Joseph Sardin) | CC0 | `yay` hype-voice role (real kid laughter) |
| Local `sfx/hype_voice/` drops | `user_provided` | Spoken-word hype roles (yes/okay/woohoo/lets_go/wow — not bundled) |

Attribution is optional; `pack.yaml` leaves `attribution` null.

## Folder layout

```
audio/
  bgm/                 # Pixabay kids beds
  sfx/
    boing/             # CC0 bounce
    sparkle/           # CC0 cute chimes
    success/           # CC0 confirm
    pop|whoosh|rimshot|fail/   # optional classic slots (empty in default)
    meme/              # user_provided meme roles (see below)
    hype_voice/        # user_provided kid-voice hype roles (see below)
  pack.yaml
```

## Kid-voice hype (`hype_voice`)

Short kid-voice reactions the planner can drop on strong kids-energy beats
(play, discovery, water play, rides, cute/laugh moments): `yes`, `okay`,
`yay`, `woohoo`, `lets_go`, `wow`.

The `yay` role ships with two real, verified files from BigSoundBank (CC0,
directly downloadable, no Cloudflare/login gate — same library the bundled
`boing` cue already uses): genuine children laughing/giggling, not a
specific word. The other five roles ship **empty**: single-word spoken
exclamations like "yes!" or "let's go!" are almost always soundboard/meme
drops (TikTok sounds, etc.) without clear redistribution rights, and the
sites that do have literal recordings of a kid saying those words (Pixabay)
sit behind Cloudflare bot protection that blocks automated fetching —
solving that programmatically wasn't attempted. A human can open those
pages in a normal browser in seconds; add what you download under
`sfx/hype_voice/` and list it in `pack.yaml` with the matching role.

A few places worth checking for genuinely license-free kid/child voice
clips (verify the license on each page before using):

- [Pixabay sound effects — "kids cheering"](https://pixabay.com/sound-effects/search/kids%20cheering/) / [Pixabay — "yay"](https://pixabay.com/sound-effects/search/yay/) (Pixabay Content License, no attribution required)
- [BigSoundBank — "enfants"](https://bigsoundbank.com/search?q=enfants) / [BigSoundBank — "bravo"](https://bigsoundbank.com/search?q=bravo) (CC0, check each sound's page)
- [Freesound.org](https://freesound.org/) — filter search results to the CC0 license explicitly; many results default to CC-BY (attribution required)

## Cue mapping

| Cue / role | When planner fires | Files |
| --- | --- | --- |
| `okay` (hype) | Soft mishap, tried before `bruh`/`fail` | empty until you add clips |
| `click` (meme) | Setting change (default) | `sfx/meme/meme-click.mp3` |
| `boom` (meme) | High-story setting punch / mid energy fallback | `sfx/meme/vine-boom-sound.mp3` |
| `yay` (hype) | Laugh / cute, tried before `goofy_laugh`/`sparkle` | BigSoundBank kid laughter (CC0) |
| `goofy_laugh` (meme) | Laugh / cute fallback | `sfx/meme/goofy-laugh-meme.mp3` |
| `sparkle` | Cute / laugh fallback | Kenney pluck, glass, confirmation |
| `wow` / `yay` (hype) | Discovery moments (look/wow/curious/explore) | `wow` empty; `yay` uses the BigSoundBank clips |
| `woohoo` / `lets_go` (hype) | Play structure, water play, rides | empty until you add clips |
| `bruh` (meme) | Soft mishap fallback | `sfx/meme/bruh-meme.mp3` |
| `fart` (meme) | Explicit silly toilet-joke keywords only | `sfx/meme/fart.mp3` |
| `yes` (hype) | Short punchy clip, tried before `click`/`pop`/`boing` | empty until you add clips |
| `lets_go` (hype) | Mid-clip energy beat, tried before `boing`/`boom` | empty until you add clips |
| `boing` | Mid-clip energy fallback | BigSoundBank boing cartoon #2/#6 |
| `whoosh` / `pop` / `fail` | Classic fallbacks when present | empty in default pack |
| `success` | Reserved / small win | Kenney confirmation, pluck |

Planner tries **hype voice / meme role first, then classic** for each moment, and
skips straight to the next candidate (or drops the cue) when a role has no files —
so the empty hype-voice roles (`yes`, `okay`, `woohoo`, `lets_go`, `wow`) fall back
to meme/classic cues exactly as before this feature existed.
Meme density is capped (~1 per 14s of edit); hype voice is capped further
(~1 per 22s) since spoken words repeat more noticeably than punctuation SFX.

## Bundled BGM (rotated across beds)

| File | Notes |
| --- | --- |
| `bgm/alex-morgan-kids-playground-giggles-parade-578468.mp3` | Kids playground bed |
| `bgm/maksymmalko-funny-kids-video-322163.mp3` | Funny kids bed |
| `bgm/sigmamusicart-kids-happy-background-music-401734.mp3` | Happy kids bed |

In `bgm.mode: beds`, the planner assigns a different candidate to consecutive beds
(round-robin, no immediate repeat when 2+ tracks exist). Volume ~0.12 under speech.
