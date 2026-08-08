from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vlog_editor.dashboard import require_approval
from vlog_editor.media import probe_video, run
from vlog_editor.project import Episode, read_json, write_json
from vlog_editor.validation import validate_render_sources


def _encoder() -> tuple[str, list[str]]:
    result = run(["ffmpeg", "-hide_banner", "-encoders"], check=False)
    if "h264_nvenc" in result.stdout:
        return "h264_nvenc", ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    return "libx264", ["-preset", "medium", "-crf", "18"]


def build_render_command(
    episode: Episode,
    plan: dict[str, Any],
    output: Path,
) -> tuple[list[str], str]:
    width = int(episode.config["output"]["width"])
    height = int(episode.config["output"]["height"])
    fps = int(episode.config["output"]["fps"])
    selections = [
        clip for section in plan["structure"] for clip in section.get("clips", [])
    ]
    if not selections:
        raise ValueError("Edit plan has no clips")

    command = ["ffmpeg", "-y", "-hide_banner"]
    metadata: list[dict[str, Any]] = []
    for clip in selections:
        start = float(clip["start"])
        duration = float(clip["end"]) - start
        source = episode.footage / str(clip["file"])
        command += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
        metadata.append({"duration": duration, "has_audio": probe_video(source)["has_audio"]})

    bgm_config = episode.config.get("bgm", {})
    bgm_value = bgm_config.get("file")
    bgm_path = None
    if bgm_value:
        candidate = Path(str(bgm_value))
        bgm_path = candidate if candidate.is_absolute() else episode.root / candidate
        if not bgm_path.is_file():
            raise FileNotFoundError(f"Configured BGM does not exist: {bgm_path}")
        command += ["-stream_loop", "-1", "-i", str(bgm_path)]

    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, item in enumerate(metadata):
        duration = item["duration"]
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
        )
        fade_out = max(0.0, duration - 0.03)
        if item["has_audio"]:
            filters.append(
                f"[{index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"atrim=duration={duration:.3f},apad=pad_dur={duration:.3f},"
                f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03[a{index}]"
            )
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
        concat_inputs.append(f"[v{index}][a{index}]")

    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(selections)}:v=1:a=1[vcat][acat]"
    )
    audio_map = "[acat]"
    if bgm_path:
        total = sum(item["duration"] for item in metadata)
        bgm_index = len(selections)
        volume = float(bgm_config.get("volume", 0.12))
        filters.extend(
            [
                (
                    f"[{bgm_index}:a]aresample=48000,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"atrim=duration={total:.3f},"
                    f"volume={volume:.3f}[music]"
                ),
                (
                    "[acat]asplit=2[original][sidechain]"
                ),
                (
                    "[music][sidechain]sidechaincompress=threshold=0.025:ratio=8:"
                    "attack=15:release=300[ducked]"
                ),
                "[original][ducked]amix=inputs=2:duration=first:normalize=0[aout]",
            ]
        )
        audio_map = "[aout]"

    codec, codec_args = _encoder()
    command += [
        "-filter_complex_script",
        str(episode.work / "render_filter.txt"),
        "-map",
        "[vcat]",
        "-map",
        audio_map,
        "-c:v",
        codec,
        *codec_args,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command, ";\n".join(filters)


def verify_output(path: Path, expected_duration: float, fps: int) -> dict[str, Any]:
    probe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(probe.stdout)
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("Rendered output has no video stream")
    if not audio:
        raise RuntimeError("Rendered output has no audio stream")
    video_duration = float(video.get("duration") or payload["format"].get("duration") or 0)
    audio_duration = float(audio.get("duration") or payload["format"].get("duration") or 0)
    drift = abs(video_duration - audio_duration)
    frames = int(video.get("nb_read_frames") or 0)
    expected_frames = round(expected_duration * fps)
    errors: list[str] = []
    if abs(video_duration - expected_duration) > max(0.12, 1.5 / fps):
        errors.append(
            f"duration {video_duration:.3f}s differs from expected {expected_duration:.3f}s"
        )
    if drift > 0.05:
        errors.append(f"A/V duration drift {drift:.3f}s exceeds 0.05s")
    if frames and abs(frames - expected_frames) > 2:
        errors.append(f"frame count {frames} differs from expected {expected_frames}")
    decode = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "NUL"], check=False)
    if decode.returncode:
        errors.append(f"decode verification failed: {decode.stderr[-500:]}")
    report = {
        "path": str(path),
        "video_duration": round(video_duration, 3),
        "audio_duration": round(audio_duration, 3),
        "av_drift": round(drift, 4),
        "frames": frames,
        "expected_frames": expected_frames,
        "errors": errors,
    }
    if errors:
        raise RuntimeError("Output verification failed:\n- " + "\n- ".join(errors))
    return report


def render_episode(episode: Episode) -> Path:
    if not episode.plan_path.is_file():
        raise FileNotFoundError("Missing edit plan. Run `ve plan` first.")
    require_approval(episode)
    plan = read_json(episode.plan_path)
    source_errors = validate_render_sources(plan, episode.footage)
    if source_errors:
        raise ValueError("\n".join(source_errors))
    episode.output.mkdir(parents=True, exist_ok=True)
    output = episode.output / "final.mp4"
    command, filter_script = build_render_command(episode, plan, output)
    filter_path = episode.work / "render_filter.txt"
    filter_path.write_text(filter_script, encoding="utf-8")
    print(f"Rendering {float(plan['duration_sec']):.1f}s with {command[command.index('-c:v') + 1]}...")
    run(command)
    report = verify_output(
        output,
        float(plan["duration_sec"]),
        int(episode.config["output"]["fps"]),
    )
    write_json(episode.output / "verification.json", report)
    print(f"Wrote {output}")
    return output
