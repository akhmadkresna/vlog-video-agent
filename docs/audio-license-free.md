# License-free audio (Leika-style Indo kids)

The framework ships a **bundled SFX + BGM pack** inspired by public Indonesian
kids/family comedy editing (e.g. Leika Garudita): short clicks/whooshes on setting
changes, pops/boings on reactions, goofy laugh / soft chimes on cute moments, plus
kids BGM beds. It does **not** rip audio from YouTube. Runtime never auto-downloads
music or SFX.

`ve new` / virgin `audio/pack.yaml` seeds files from
`src/vlog_editor/assets/default_audio/` (see `SOURCES.md` there).

## Allowed licenses

- `youtube_audio_library` — YouTube Studio Audio Library
- `cc0` — Creative Commons Zero
- `public_domain`
- `pixabay` — Pixabay Content License
- `original` — recordings you own and mark free for this project
- `user_provided` — local meme drops under `sfx/meme/` (pack owner accepts use; not CC0)

## Bundled defaults

| Cue / role | Source |
| --- | --- |
| `boing` | [BigSoundBank](https://bigsoundbank.com/) cartoon boings (CC0) |
| `sparkle` / `success` | [Kenney](https://kenney.nl/assets/interface-sounds) (CC0) |
| `pop` / `whoosh` / `rimshot` / `fail` | Optional classic slots (empty in default) |
| `click` / `boom` / `bruh` / `goofy_laugh` / `fart` | `sfx/meme/` (`user_provided`) |

Meme files stay in one folder (`sfx/meme/`) and declare a `role` in `pack.yaml`.
The planner tries meme roles first, then classic types, with a meme density cap.

### Bundled BGM (Pixabay Content License)

Three kids beds under `audio/bgm/`; in `beds` mode the planner **rotates** them
across windows (no immediate repeat when 2+ tracks exist).

Default `bgm.mode` is `beds` (not whole video): intro, playing/fooling-around,
light B-roll / transitions, and outro — capped around ~45% coverage, with fades.
Set `bgm.mode: full` in `project.yaml` for a continuous ducked bed.
Pin one track with `bgm.file: audio/bgm/<name>.mp3` to disable rotation.

## Recommended extra BGM searches (YouTube Audio Library)

Soft beds that duck under speech:

- playful ukulele kids
- xylophone cartoon
- pizzicato happy
- light acoustic children
- cheerful folk no lyrics

Prefer filters: **no attribution required** when available.

## Extra SFX searches (if you want more)

| Cue type | Search terms |
| --- | --- |
| `boing` | cartoon boing, spring, bounce |
| `pop` | pop, bubble pop, soft click |
| `whoosh` | whoosh, swoosh, transition whoosh |
| `sparkle` | sparkle, twinkle, magic chime |
| `rimshot` | rimshot, comedy drum |
| `fail` | soft buzzer, fail horn (soft) |
| `success` | success chime, ding, reward |
| meme roles | vine boom, bruh, goofy laugh, click, fart |

Do **not** auto-scrape CapCut / YouTube trending sounds. Drop meme files you accept
using into `audio/sfx/meme/` and declare them with `license: user_provided` + `role`.

## Pack example

```yaml
style: funny_kids_indo
license_policy: license_free_only
bgm_candidates:
  - file: bgm/kids_bed.m4a
    license: youtube_audio_library
    attribution: null
    source_url: https://studio.youtube.com/
sfx:
  sparkle:
    gain: 0.40
    files:
      - file: sfx/sparkle/sparkle_01.wav
        license: cc0
        attribution: null
        source_url: https://example.com/source
  meme:
    gain: 0.55
    files:
      - file: sfx/meme/vine-boom-sound.mp3
        role: boom
        license: user_provided
```

To re-seed the bundled pack into an episode with an empty declaration list, delete
or empty `audio/pack.yaml` file lists and run `ve plan` (or call
`seed_bundled_default_pack(..., force=True)` from a script).
