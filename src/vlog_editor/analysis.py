from __future__ import annotations

import shutil
import time
from typing import Any

from vlog_editor.asr import Transcriber
from vlog_editor.cache import JsonCache, cache_key
from vlog_editor.media import detect_volume, extract_frames, list_videos, probe_video
from vlog_editor.project import Episode, write_json
from vlog_editor.vision import OllamaClient


def _metric_seconds(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1_000_000_000, 3)


def analyze_episode(episode: Episode, *, force: bool = False) -> dict[str, Any]:
    videos = list_videos(episode.footage)
    if not videos:
        raise FileNotFoundError(f"No video files found in {episode.footage}")

    cache = JsonCache(episode.work / "cache")
    records: dict[str, dict[str, Any]] = {}
    asr_config = dict(episode.config["asr"])
    language = str(episode.config.get("language", "auto"))

    # ASR is a separate phase so its CUDA allocation is released before Ollama loads.
    with Transcriber(asr_config) as transcriber:
        for index, path in enumerate(videos, start=1):
            print(f"[ASR {index}/{len(videos)}] {path.name}")
            metadata = probe_video(path)
            volume = detect_volume(path) if metadata["has_audio"] else {"mean_db": None, "max_db": None}
            key = cache_key(path, "asr", {"config": asr_config, "language": language})
            transcript = None if force else cache.get("asr", key)
            if transcript is None:
                if metadata["has_audio"] and volume["mean_db"] is not None:
                    transcript = transcriber.transcribe(path, language=language)
                else:
                    transcript = {
                        "language": language,
                        "language_probability": 0,
                        "segments": [],
                        "words": [],
                        "text": "",
                    }
                cache.put("asr", key, transcript)
            records[path.name] = {
                "path": path,
                "metadata": metadata,
                "audio": {**volume, **transcript},
            }

    vision_config = dict(episode.config["vision"])
    frame_width = int(vision_config.get("frame_width", 1280))
    client = OllamaClient(vision_config)
    try:
        for index, path in enumerate(videos, start=1):
            print(f"[VISION {index}/{len(videos)}] {path.name}")
            record = records[path.name]
            key = cache_key(
                path,
                "vision",
                {
                    "model": vision_config["model"],
                    "frame_width": frame_width,
                    "sampling": "v4-capped-setting-focused-ranges",
                },
            )
            cached = None if force else cache.get("vision", key)
            if cached is None:
                frame_dir = episode.work / "frames" / f"{index:04d}"
                if force and frame_dir.exists():
                    shutil.rmtree(frame_dir)
                frames = extract_frames(
                    path,
                    frame_dir,
                    float(record["metadata"]["duration"]),
                    width=frame_width,
                )
                started = time.perf_counter()
                visual, metrics = client.analyze_clip(
                    frames,
                    filename=path.name,
                    duration=float(record["metadata"]["duration"]),
                    transcript=str(record["audio"]["text"]),
                )
                cached = {
                    "visual": visual,
                    "frames": [str(item.relative_to(episode.root)) for item in frames],
                    "timing": {
                        "wall_sec": round(time.perf_counter() - started, 3),
                        "ollama_total_sec": _metric_seconds(metrics.get("total_duration")),
                        "ollama_load_sec": _metric_seconds(metrics.get("load_duration")),
                        "output_tokens": metrics.get("eval_count"),
                    },
                }
                cache.put("vision", key, cached)
            record.update(cached)
    finally:
        client.unload()

    output_records: list[dict[str, Any]] = []
    for path in videos:
        record = records[path.name]
        record.pop("path", None)
        output_records.append(record)
    result = {
        "schema_version": 1,
        "model": vision_config["model"],
        "clip_count": len(output_records),
        "clips": output_records,
    }
    write_json(episode.analysis_path, result)
    print(f"Wrote {episode.analysis_path}")
    return result
