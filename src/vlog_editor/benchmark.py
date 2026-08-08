from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from vlog_editor.project import Episode, write_json
from vlog_editor.vision import OllamaClient, parse_json_response


def _synthetic_frames(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    labels = ("Arrival at station", "Walking through market", "Sunset viewpoint")
    paths: list[Path] = []
    for index, label in enumerate(labels, start=1):
        path = folder / f"benchmark_{index}.jpg"
        image = Image.new("RGB", (1280, 720), (25 * index, 65 + 20 * index, 105))
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 1180, 620), outline="white", width=8)
        draw.text((150, 320), label, fill="white")
        image.save(path, quality=88)
        paths.append(path)
    return paths


def run_benchmark(episode: Episode) -> dict[str, Any]:
    frames = _synthetic_frames(episode.work / "benchmark")
    client = OllamaClient(dict(episode.config["vision"]))
    prompt = (
        "These are three chronological travel-vlog frames. Return only JSON with "
        '{"summary":"one concise sequence description","best_frame":1}.'
    )
    try:
        # Warm-up isolates steady-state latency from one-time model loading.
        client.chat('Return only {"ready":true}.', timeout=600)
        started = time.perf_counter()
        content, metrics = client.chat(prompt, images=frames, timeout=600)
        wall = time.perf_counter() - started
        parsed = parse_json_response(content)
        eval_count = metrics.get("eval_count")
        eval_duration = metrics.get("eval_duration")
        tokens_per_sec = None
        if isinstance(eval_count, (int, float)) and isinstance(eval_duration, (int, float)):
            tokens_per_sec = float(eval_count) / (float(eval_duration) / 1_000_000_000)
        result = {
            "model": client.model,
            "frame_count": len(frames),
            "frame_size": "1280x720",
            "warm_wall_sec": round(wall, 3),
            "output_tokens": eval_count,
            "tokens_per_sec": round(tokens_per_sec, 2) if tokens_per_sec else None,
            "response": parsed,
        }
        write_json(episode.work / "benchmark.json", result)
        return result
    finally:
        client.unload()
