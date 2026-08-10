from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _default_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    else:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise TypeError("Model response must be a JSON object")
    return value


class OllamaClient:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: Transport = _default_transport,
    ) -> None:
        self.endpoint = str(config["endpoint"]).rstrip("/")
        self.model = str(config["model"])
        self.max_tokens = int(config.get("max_output_tokens", 240))
        self.keep_alive = str(config.get("keep_alive", "30m"))
        self.transport = transport

    def chat(
        self,
        prompt: str,
        *,
        images: list[Path] | None = None,
        timeout: float = 300,
        retries: int = 2,
        max_tokens: int | None = None,
        context_tokens: int = 16384,
    ) -> tuple[str, dict[str, Any]]:
        encoded = [
            base64.b64encode(path.read_bytes()).decode("ascii") for path in (images or [])
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": encoded}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "format": "json",
            "options": {
                "temperature": 0.15,
                "num_predict": max_tokens or self.max_tokens,
                "num_ctx": context_tokens,
            },
        }
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = self.transport(f"{self.endpoint}/api/chat", payload, timeout)
                content = str(result.get("message", {}).get("content", ""))
                if not content:
                    raise ValueError("Ollama returned an empty response")
                metrics = {
                    key: result.get(key)
                    for key in (
                        "total_duration",
                        "load_duration",
                        "prompt_eval_count",
                        "prompt_eval_duration",
                        "eval_count",
                        "eval_duration",
                    )
                }
                return content, metrics
            except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Ollama request failed after {retries + 1} attempts: {last_error}")

    def analyze_clip(
        self,
        frames: list[Path],
        *,
        filename: str,
        duration: float,
        transcript: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        prompt = f"""
You are analyzing chronologically ordered frames from one travel-vlog clip.
Filename: {filename}
Duration: {duration:.2f} seconds
Local speech transcript: {transcript[:1600] or "(none)"}

Return only compact JSON with this schema:
{{
  "summary": "one sentence describing what happens across the clip",
  "subjects": ["people, places, objects"],
  "setting": "vehicle|home|street|outdoor|mall|store|restaurant|hotel|transit|nature|attraction|other",
  "shot_type": "wide|full|medium|close|detail|mixed",
  "camera": "static|handheld|pan|tracking|mixed",
  "mood": "short mood",
  "quality": 0.0,
  "motion": 0.0,
  "story_value": 0.0,
  "kids_audience_value": 0.0,
  "kids_hooks": ["play_structure|animals|water_play|treats|discovery|ride_fun"],
  "issues": ["blur, shake, obstruction, duplicate, poor exposure, or empty"],
  "recommended_ranges": [{{"start": 0.0, "end": 8.0, "reason": "why this exact moment"}}]
}}
Scores are 0..1. kids_audience_value rates how engaging the beat is for a kids audience
using activity categories in kids_hooks (not specific place names). Prefer recommended
ranges where children are on camera or a kids-audience activity is happening. Do not
invent speech. Keep ranges inside the clip. Return 1-4 strongest ranges in best-first
order. Pure visual ranges should be 2-8 seconds; spoken ranges may be 5-30 seconds and
must preserve a complete exchange. For clips longer than 60 seconds, sample moments
across the clip and never recommend the entire clip or default every range to 0.
""".strip()
        content, metrics = self.chat(prompt, images=frames)
        return parse_json_response(content), metrics

    def unload(self) -> None:
        payload = {"model": self.model, "keep_alive": 0}
        try:
            self.transport(f"{self.endpoint}/api/generate", payload, 15)
        except (OSError, ValueError, urllib.error.URLError):
            return
