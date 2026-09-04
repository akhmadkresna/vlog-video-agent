from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _ollama_tags(endpoint: str) -> list[str]:
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=3) as response:
            payload = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    return [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]


def inspect_machine(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    problems: list[str] = []

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        (ok if path else problems).append(
            f"{tool}: {path}" if path else f"{tool}: missing from PATH"
        )

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        encoders = _run([ffmpeg, "-hide_banner", "-encoders"]).stdout
        if "h264_nvenc" in encoders:
            ok.append("NVENC: h264_nvenc available")
        else:
            problems.append("NVENC: h264_nvenc unavailable; rendering will use libx264")

        # The render passes its filtergraph with "-/filter_complex FILE".
        # "-filter_complex_script" was removed in ffmpeg 7.0, and the "-/opt"
        # file-read form needs ffmpeg >= 5.1 — verify this build is new enough
        # rather than discovering it only when a render aborts on frame 1.
        version_line = _run([ffmpeg, "-hide_banner", "-version"]).stdout.splitlines()
        version = version_line[0].strip() if version_line else "ffmpeg version unknown"
        match = re.search(r"version\s+n?(\d+)\.(\d+)", version)
        if match and (int(match.group(1)), int(match.group(2))) < (5, 1):
            problems.append(
                f"ffmpeg too old for '-/filter_complex' (need >= 5.1): {version}. "
                "Render aborts at start — upgrade ffmpeg."
            )
        else:
            ok.append(f"ffmpeg filtergraph syntax OK ('-/filter_complex'): {version}")

        # 4K/60 HEVC camera footage is unwatchably slow to software-decode; the
        # render relies on "-hwaccel cuda" (NVDEC). Flag a build without it so a
        # multi-hour render is a choice, not a surprise.
        hwaccels = _run([ffmpeg, "-hide_banner", "-hwaccels"]).stdout
        if "cuda" in hwaccels:
            ok.append("GPU decode: -hwaccel cuda available (NVDEC)")
        else:
            problems.append(
                "GPU decode: cuda hwaccel unavailable; 4K source renders will be very slow "
                "(set output.hwaccel: none to silence, or install an ffmpeg with NVDEC)"
            )

    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        gpu = _run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        )
        if gpu.returncode == 0:
            ok.append(f"GPU: {gpu.stdout.strip()}")
        else:
            problems.append("GPU: nvidia-smi failed")
    else:
        problems.append("GPU: nvidia-smi missing; CUDA ASR may not work")

    try:
        import faster_whisper  # noqa: F401

        ok.append("ASR: faster-whisper import succeeded")
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        problems.append(f"ASR: faster-whisper unavailable ({exc})")

    vision = config["vision"]
    endpoint = str(vision["endpoint"])
    model = str(vision["model"])
    tags = _ollama_tags(endpoint)
    if tags:
        ok.append(f"Ollama: reachable at {endpoint}")
        normalized = {tag.split(":latest")[0] for tag in tags}
        if model in tags or model in normalized:
            ok.append(f"Vision model: {model} installed")
        else:
            problems.append(f"Vision model missing: run `ollama pull {model}`")
    else:
        problems.append("Ollama: not reachable; install/start Ollama for Windows")

    return ok, problems


def print_report(config: dict[str, Any]) -> int:
    from vlog_editor.youtube import youtube_ready

    ok, problems = inspect_machine(config)
    yt_ok, yt_warn = youtube_ready()
    print("Local Vlog Editor doctor\n")
    for line in [*ok, *yt_ok]:
        print(f"OK   {line}")
    for line in [*problems, *yt_warn]:
        print(f"WARN {line}")
    return 0 if not problems else 1
