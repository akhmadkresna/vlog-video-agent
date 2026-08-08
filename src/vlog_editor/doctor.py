from __future__ import annotations

import json
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
    ok, problems = inspect_machine(config)
    print("Local Vlog Editor doctor\n")
    for line in ok:
        print(f"OK   {line}")
    for line in problems:
        print(f"WARN {line}")
    return 0 if not problems else 1
