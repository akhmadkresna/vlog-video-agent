from __future__ import annotations

from pathlib import Path

from vlog_editor.vision import OllamaClient, parse_json_response


def test_parse_json_response_accepts_fenced_json() -> None:
    assert parse_json_response('```json\n{"summary":"street"}\n```') == {"summary": "street"}


def test_multi_image_request_uses_one_chat_call(tmp_path: Path) -> None:
    frames = [tmp_path / f"{index}.jpg" for index in range(3)]
    for index, frame in enumerate(frames):
        frame.write_bytes(f"frame-{index}".encode())
    calls: list[dict] = []

    def transport(url: str, payload: dict, timeout: float) -> dict:
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        return {
            "message": {
                "content": (
                    '{"summary":"walking","subjects":["person"],"shot_type":"wide",'
                    '"camera":"handheld","mood":"bright","quality":0.8,"motion":0.7,'
                    '"story_value":0.7,"issues":[],"recommended_ranges":'
                    '[{"start":0,"end":5,"reason":"clear"}]}'
                )
            },
            "eval_count": 30,
        }

    client = OllamaClient(
        {
            "endpoint": "http://127.0.0.1:11434",
            "model": "qwen3-vl:4b-instruct",
            "max_output_tokens": 200,
        },
        transport=transport,
    )
    result, metrics = client.analyze_clip(
        frames, filename="clip.mp4", duration=5, transcript=""
    )
    assert result["summary"] == "walking"
    assert metrics["eval_count"] == 30
    assert len(calls) == 1
    assert len(calls[0]["payload"]["messages"][0]["images"]) == 3
