from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Self


class Transcriber:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                str(self.config.get("model", "large-v3-turbo")),
                device=str(self.config.get("device", "cuda")),
                compute_type=str(self.config.get("compute_type", "int8_float16")),
            )
        return self._model

    def transcribe(self, media_path: Path, language: str = "auto") -> dict[str, Any]:
        model = self._load()
        segments_iter, info = model.transcribe(
            str(media_path),
            language=None if language == "auto" else language,
            vad_filter=True,
            word_timestamps=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        segments: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        for segment in segments_iter:
            text = segment.text.strip()
            if text:
                segments.append(
                    {
                        "start": round(float(segment.start), 3),
                        "end": round(float(segment.end), 3),
                        "text": text,
                    }
                )
            for word in segment.words or []:
                if word.start is None or word.end is None:
                    continue
                words.append(
                    {
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                        "text": word.word.strip(),
                        "probability": round(float(word.probability), 4),
                    }
                )
        return {
            "language": getattr(info, "language", language),
            "language_probability": round(float(getattr(info, "language_probability", 0)), 4),
            "segments": segments,
            "words": words,
            "text": " ".join(item["text"] for item in segments),
        }

    def close(self) -> None:
        self._model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
