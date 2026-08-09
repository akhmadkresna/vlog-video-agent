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
  pack.yaml
```

## Cue mapping

| Cue / role | When planner fires | Files |
| --- | --- | --- |
| `click` (meme) | Setting change (default) | `sfx/meme/meme-click.mp3` |
| `boom` (meme) | High-story setting punch / mid energy fallback | `sfx/meme/vine-boom-sound.mp3` |
| `goofy_laugh` (meme) | Laugh / cute (preferred over sparkle) | `sfx/meme/goofy-laugh-meme.mp3` |
| `sparkle` | Cute / laugh fallback | Kenney pluck, glass, confirmation |
| `bruh` (meme) | Soft mishap | `sfx/meme/bruh-meme.mp3` |
| `fart` (meme) | Explicit silly toilet-joke keywords only | `sfx/meme/fart.mp3` |
| `boing` | Mid-clip energy | BigSoundBank boing cartoon #2/#6 |
| `whoosh` / `pop` / `fail` | Classic fallbacks when present | empty in default pack |
| `success` | Reserved / small win | Kenney confirmation, pluck |

Planner tries **meme role first, then classic** for each moment. Meme density is
capped (~1 per 14s of edit) so vine-boom / violin do not spam.

## Bundled BGM (rotated across beds)

| File | Notes |
| --- | --- |
| `bgm/alex-morgan-kids-playground-giggles-parade-578468.mp3` | Kids playground bed |
| `bgm/maksymmalko-funny-kids-video-322163.mp3` | Funny kids bed |
| `bgm/sigmamusicart-kids-happy-background-music-401734.mp3` | Happy kids bed |

In `bgm.mode: beds`, the planner assigns a different candidate to consecutive beds
(round-robin, no immediate repeat when 2+ tracks exist). Volume ~0.12 under speech.
