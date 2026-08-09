from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vlog_editor.dashboard import require_approval
from vlog_editor.media import probe_video, run
from vlog_editor.project import Episode, read_json, write_json
from vlog_editor.validation import validate_audio_plan, validate_render_sources


def _encoder() -> tuple[str, list[str]]:
    result = run(["ffmpeg", "-hide_banner", "-encoders"], check=False)
    if "h264_nvenc" in result.stdout:
        return "h264_nvenc", ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    return "libx264", ["-preset", "medium", "-crf", "18"]


def _resolve_audio_path(episode: Episode, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    return episode.root / relative


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

    total = sum(item["duration"] for item in metadata)
    cues = [cue for cue in plan.get("audio_cues", []) or [] if isinstance(cue, dict)]
    cue_paths: list[Path] = []
    for cue in cues:
        path = _resolve_audio_path(episode, str(cue["file"]))
        if not path.is_file():
            raise FileNotFoundError(f"SFX file missing: {path}")
        cue_paths.append(path)
        command += ["-i", str(path)]

    bgm_payload = plan.get("bgm") if isinstance(plan.get("bgm"), dict) else None
    bgm_config = episode.config.get("bgm", {})
    bgm_path = None
    bgm_volume = float(bgm_config.get("volume", 0.12))
    bgm_segments: list[dict[str, Any]] = []
    if bgm_payload and (bgm_payload.get("file") or bgm_payload.get("segments")):
        if bgm_payload.get("file"):
            bgm_path = _resolve_audio_path(episode, str(bgm_payload["file"]))
        bgm_volume = float(bgm_payload.get("volume", bgm_volume))
        raw_segments = bgm_payload.get("segments") or []
        if isinstance(raw_segments, list):
            bgm_segments = [item for item in raw_segments if isinstance(item, dict)]
    elif bgm_config.get("file"):
        candidate = Path(str(bgm_config["file"]))
        bgm_path = candidate if candidate.is_absolute() else episode.root / candidate
    bgm_input_paths: list[Path] = []
    if bgm_segments:
        for segment in bgm_segments:
            relative = str(segment.get("file") or (bgm_payload or {}).get("file") or "")
            path = _resolve_audio_path(episode, relative)
            if not path.is_file():
                raise FileNotFoundError(f"BGM bed file missing: {path}")
            bgm_input_paths.append(path)
    elif bgm_path is not None:
        if not bgm_path.is_file():
            raise FileNotFoundError(f"Configured BGM does not exist: {bgm_path}")
        bgm_input_paths = [bgm_path]
    for path in bgm_input_paths:
        command += ["-stream_loop", "-1", "-i", str(path)]

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

    dialogue_label = "[acat]"
    next_input = len(selections)
    if cue_paths:
        sfx_labels: list[str] = []
        for offset, cue in enumerate(cues):
            input_index = next_input + offset
            delay_ms = max(0, round(float(cue["at_sec"]) * 1000))
            gain = float(cue.get("gain", 0.5))
            label = f"sfx{offset}"
            filters.append(
                f"[{input_index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={gain:.3f},"
                f"adelay={delay_ms}|{delay_ms},"
                f"apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            sfx_labels.append(f"[{label}]")
        if len(sfx_labels) == 1:
            filters.append(f"{sfx_labels[0]}anull[sfxmix]")
        else:
            filters.append(
                "".join(sfx_labels)
                + f"amix=inputs={len(sfx_labels)}:duration=longest:normalize=0[sfxmix]"
            )
        filters.append(
            "[acat][sfxmix]amix=inputs=2:duration=first:normalize=0[dialogue_fx]"
        )
        dialogue_label = "[dialogue_fx]"
        next_input += len(cue_paths)

    # Light dialogue loudness normalize so speech stays clear under beds/SFX.
    filters.append(
        f"{dialogue_label}dynaudnorm=f=150:g=12:p=0.9[dialogue_norm]"
    )
    dialogue_label = "[dialogue_norm]"

    audio_map = dialogue_label
    if bgm_input_paths:
        if bgm_segments:
            bed_labels: list[str] = []
            for offset, segment in enumerate(bgm_segments):
                input_index = next_input + offset
                start = float(segment["start_sec"])
                end = float(segment["end_sec"])
                seg_dur = max(0.05, end - start)
                fade = min(0.6, seg_dur / 3.0)
                fade_out = max(0.0, seg_dur - fade)
                delay_ms = max(0, round(start * 1000))
                volume = float(segment.get("volume", bgm_volume))
                label = f"bed{offset}"
                filters.append(
                    f"[{input_index}:a]aresample=48000,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"atrim=duration={seg_dur:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fade:.3f},"
                    f"afade=t=out:st={fade_out:.3f}:d={fade:.3f},"
                    f"volume={volume:.3f},"
                    f"adelay={delay_ms}|{delay_ms},"
                    f"apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                    f"asetpts=PTS-STARTPTS[{label}]"
                )
                bed_labels.append(f"[{label}]")
            if len(bed_labels) == 1:
                filters.append(f"{bed_labels[0]}anull[music]")
            else:
                filters.append(
                    "".join(bed_labels)
                    + f"amix=inputs={len(bed_labels)}:duration=longest:normalize=0[music]"
                )
        else:
            bgm_index = next_input
            filters.append(
                f"[{bgm_index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"atrim=duration={total:.3f},"
                f"volume={bgm_volume:.3f}[music]"
            )
        filters.extend(
            [
                f"{dialogue_label}asplit=2[original][sidechain]",
                (
                    "[music][sidechain]sidechaincompress=threshold=0.015:ratio=12:"
                    "attack=8:release=220[ducked]"
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
        "-t",
        f"{total:.3f}",
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
    if abs(video_duration - expected_duration) > max(0.25, 2.0 / fps):
        errors.append(
            f"duration {video_duration:.3f}s differs from expected {expected_duration:.3f}s"
        )
    if drift > 0.08:
        errors.append(f"A/V duration drift {drift:.3f}s exceeds 0.08s")
    if frames and abs(frames - expected_frames) > max(3, round(fps * 0.1)):
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
    density = str(episode.config.get("audio", {}).get("sfx_density", "medium")).lower()
    min_gap = {"light": 10.0, "medium": 6.0, "heavy": 3.5}.get(density, 6.0)
    audio_errors = validate_audio_plan(plan, episode.root, min_gap=min_gap)
    if audio_errors:
        raise ValueError("\n".join(audio_errors))
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
