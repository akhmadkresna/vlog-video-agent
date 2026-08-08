# Local Vlog Editor Agent

Windows-native, local-only travel/B-roll editing pipeline:

`footage → faster-whisper → Qwen3-VL → edit plan → review → NVENC render`

No footage, frames, transcripts, or prompts are sent to a paid service. Ollama listens on
`127.0.0.1`, and final rendering is blocked until the exact edit plan has been approved.

## Optimized machine profile

- NVIDIA RTX 4060 8 GB
- 32 GB RAM
- Windows FFmpeg with `h264_nvenc`
- Ollama `qwen3-vl:4b-instruct` (Q4, about 3.3 GB)
- faster-whisper `large-v3-turbo` with CUDA `int8_float16`

ASR and vision run as separate phases so their models do not compete for VRAM.

## Setup

```powershell
winget install Ollama.Ollama
ollama pull qwen3-vl:4b-instruct
cd D:\AI\vlog-editor-agent
uv sync
uv run ve doctor
```

## First project

```powershell
uv run ve new D:\Videos\my-trip
# Copy source videos into D:\Videos\my-trip\footage
uv run ve run D:\Videos\my-trip
```

Review `work/dashboard/index.html`. Rendering requires two explicit commands:

```powershell
uv run ve approve D:\Videos\my-trip
uv run ve render D:\Videos\my-trip
```

If `work/edit_plan.json` changes after approval, rendering is blocked until it is reviewed and
approved again.

## Commands

- `ve doctor [episode]`: check FFmpeg, CUDA, faster-whisper, Ollama and the model.
- `ve new <path>`: create `project.yaml`, `footage/`, `work/` and `output/`.
- `ve analyze [episode]`: cache transcription and multi-frame visual analysis.
- `ve plan [episode]`: generate and deterministically validate an edit plan.
- `ve preview [episode]`: generate/open the local dashboard.
- `ve approve [episode]`: approve the SHA-256 digest of the current plan.
- `ve render [episode]`: render and verify `output/final.mp4`.
- `ve run [episode]`: analyze, plan, preview and stop for review.
- `ve benchmark [episode]`: measure a warm three-frame Qwen vision request.

## Performance

Visual analysis makes one multi-image request per clip, not one request per frame. Results are
content-addressed, so retries and subsequent edits do not repeat unchanged analysis.

Machine-specific measured latency will be recorded here after the live benchmark:

- Model: `qwen3-vl:4b-instruct`
- Input: three 1280px-wide frames
- Warm request latency: 10.76 seconds
- Decode throughput: 71.08 tokens/second

The one-time cold model load took about 45 seconds during this benchmark. Keep-alive is set to
30 minutes, so normal batch analysis pays that cost once rather than once per clip. At the
measured warm rate, 80 ordinary clips are approximately 14-15 minutes of vision inference,
plus frame extraction and transcription.

## Episode layout

```text
my-trip/
├── project.yaml
├── footage/                 # read-only source videos
├── work/
│   ├── cache/
│   ├── frames/
│   ├── clip_analysis.json
│   ├── edit_plan.json
│   ├── approval.json
│   └── dashboard/index.html
└── output/
    ├── final.mp4
    └── verification.json
```

## Attribution

The staged workflow and several editing safeguards were informed by
[`znyupup/ai-video-editing-skill`](https://github.com/znyupup/ai-video-editing-skill),
used under its MIT license. This implementation is new code and adds a runnable Windows CLI,
local inference, caching, validation, approval gating, NVENC rendering and automated tests.
