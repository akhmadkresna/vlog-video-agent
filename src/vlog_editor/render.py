from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from vlog_editor.audio_pack import (
    HYPE_VOICE_ROLES,
    MEME_ROLES,
    ensure_audio_pack_layout,
    hype_voice_files_for_role,
    load_audio_pack,
    meme_files_for_role,
    resolve_pack_file,
)
from vlog_editor.captions import ffmpeg_subtitles_path, prepare_caption_files
from vlog_editor.dashboard import require_approval
from vlog_editor.media import probe_video, run
from vlog_editor.project import DEFAULT_BGM_VOLUME, Episode, read_json, write_json
from vlog_editor.transitions import (
    bundled_card_image_path,
    plan_time_skip_cards,
    shift_time,
    transitions_config,
)
from vlog_editor.validation import validate_audio_plan, validate_render_sources


def _transition_sfx_cues(
    episode: Episode,
    cards: list[dict[str, Any]],
    transitions: dict[str, Any],
) -> list[dict[str, Any]]:
    """One SFX cue per time-skip card, in the same content-time domain as
    plan['audio_cues'] so it rides the existing shift_time/adelay handling."""
    sfx_type = str(transitions.get("sfx_type") or "").strip()
    if not cards or not sfx_type:
        return []
    ensure_audio_pack_layout(episode.root)
    pack_rel = str(episode.config.get("audio", {}).get("pack", "audio/pack.yaml"))
    pack = load_audio_pack(episode.root, pack_rel)
    if sfx_type in MEME_ROLES:
        block = pack["sfx"].get("meme", {})
        files = meme_files_for_role(pack, sfx_type)
    elif sfx_type in HYPE_VOICE_ROLES:
        block = pack["sfx"].get("hype_voice", {})
        files = hype_voice_files_for_role(pack, sfx_type)
    else:
        block = pack["sfx"].get(sfx_type, {})
        files = list(block.get("files", []) or [])
    if not files:
        return []
    chosen = files[0]
    relative = str(chosen["file"])
    if not relative.startswith("audio/"):
        relative = f"audio/{relative.lstrip('/')}"
    path = resolve_pack_file(episode.root, relative)
    if not path.is_file():
        return []
    gain = float(transitions.get("sfx_gain") or chosen.get("gain", block.get("gain", 0.5)))
    return [
        {
            "at_sec": float(card["content_time"]),
            "file": relative.replace("\\", "/"),
            "gain": gain,
            "type": sfx_type,
        }
        for card in cards
    ]


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
    *,
    burn_in_captions: Path | None = None,
    time_skip_cards: list[dict[str, Any]] | None = None,
) -> tuple[list[str], str, float]:
    width = int(episode.config["output"]["width"])
    height = int(episode.config["output"]["height"])
    fps = int(episode.config["output"]["fps"])
    selections = [
        clip for section in plan["structure"] for clip in section.get("clips", [])
    ]
    if not selections:
        raise ValueError("Edit plan has no clips")

    cards = time_skip_cards or []
    cards_by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        cards_by_index[int(card["after_index"])].append(card)

    command = ["ffmpeg", "-y", "-hide_banner"]
    metadata: list[dict[str, Any]] = []
    for clip in selections:
        start = float(clip["start"])
        duration = float(clip["end"]) - start
        source = episode.footage / str(clip["file"])
        command += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
        metadata.append(
            {
                "duration": duration,
                "has_audio": probe_video(source)["has_audio"],
                "gameplay": clip.get("gameplay")
                if isinstance(clip.get("gameplay"), dict)
                else None,
            }
        )

    gameplay_input_count = 0
    for item in metadata:
        gameplay = item.get("gameplay")
        if not isinstance(gameplay, dict):
            continue
        game_path = episode.gameplay / str(gameplay["file"])
        if not game_path.is_file():
            raise FileNotFoundError(f"Gameplay file missing: {game_path}")
        game_duration = float(gameplay["duration_sec"])
        command += [
            "-ss",
            f"{float(gameplay['game_start_sec']):.3f}",
            "-t",
            f"{game_duration:.3f}",
            "-i",
            str(game_path),
        ]
        item["gameplay_input_index"] = len(selections) + gameplay_input_count
        gameplay_input_count += 1

    content_total = sum(item["duration"] for item in metadata)
    card_total = sum(float(card["duration"]) for card in cards)
    total = content_total + card_total
    transitions = transitions_config(episode.config)
    cues = [cue for cue in plan.get("audio_cues", []) or [] if isinstance(cue, dict)]
    cues += _transition_sfx_cues(episode, cards, transitions)
    cues.sort(key=lambda cue: float(cue["at_sec"]))
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
    bgm_volume = float(bgm_config.get("volume", DEFAULT_BGM_VOLUME))
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
    bgm_segment_input_indexes: list[int] = []
    if bgm_segments:
        # One ffmpeg input per unique bed file (not per segment) to cut RAM/handles.
        unique_index: dict[Path, int] = {}
        for segment in bgm_segments:
            relative = str(segment.get("file") or (bgm_payload or {}).get("file") or "")
            path = _resolve_audio_path(episode, relative)
            if not path.is_file():
                raise FileNotFoundError(f"BGM bed file missing: {path}")
            resolved = path.resolve()
            if resolved not in unique_index:
                unique_index[resolved] = len(bgm_input_paths)
                bgm_input_paths.append(path)
            bgm_segment_input_indexes.append(unique_index[resolved])
    elif bgm_path is not None:
        if not bgm_path.is_file():
            raise FileNotFoundError(f"Configured BGM does not exist: {bgm_path}")
        bgm_input_paths = [bgm_path]
    for path in bgm_input_paths:
        command += ["-stream_loop", "-1", "-i", str(path)]

    card_image_path = bundled_card_image_path()
    if cards and not card_image_path.is_file():
        raise FileNotFoundError(f"Time-skip card image missing: {card_image_path}")
    next_free_input = len(selections) + gameplay_input_count + len(cue_paths) + len(bgm_input_paths)

    filters: list[str] = []
    concat_inputs: list[str] = []
    card_counter = 0
    pip_config = episode.config.get("gameplay", {}).get("pip", {})
    pip_ratio = max(0.15, min(0.5, float(pip_config.get("width_ratio", 0.28))))
    pip_width = max(2, round(width * pip_ratio / 2) * 2)
    pip_height = max(2, round((pip_width * height / width) / 2) * 2)
    pip_margin = max(0, int(pip_config.get("margin", 40)))
    pip_border = max(0, int(pip_config.get("border", 6)))
    pip_position = str(pip_config.get("position", "bottom_right")).lower()
    pip_outer_width = pip_width + 2 * pip_border
    pip_outer_height = pip_height + 2 * pip_border
    pip_x = pip_margin if "left" in pip_position else width - pip_outer_width - pip_margin
    pip_y = pip_margin if "top" in pip_position else height - pip_outer_height - pip_margin
    for index, item in enumerate(metadata):
        duration = item["duration"]
        video_normalize = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,format=yuv420p,setpts=PTS-STARTPTS"
        )
        gameplay = item.get("gameplay")
        if isinstance(gameplay, dict):
            game_index = int(item["gameplay_input_index"])
            offset = float(gameplay["camera_offset_sec"])
            game_duration = float(gameplay["duration_sec"])
            game_end = offset + game_duration
            filters.append(
                f"[{index}:v]{video_normalize},split=2[cam_base{index}][cam_pip{index}]"
            )
            filters.append(
                f"[{game_index}:v]{video_normalize},"
                f"setpts=PTS+{offset:.3f}/TB[game{index}]"
            )
            filters.append(
                f"[cam_base{index}][game{index}]overlay=0:0:eof_action=pass:"
                f"enable='between(t,{offset:.3f},{game_end:.3f})'[game_base{index}]"
            )
            filters.append(
                f"[cam_pip{index}]scale={pip_width}:{pip_height},"
                f"pad={pip_outer_width}:{pip_outer_height}:{pip_border}:{pip_border}:white"
                f"[pip{index}]"
            )
            filters.append(
                f"[game_base{index}][pip{index}]overlay={pip_x}:{pip_y}:eof_action=pass:"
                f"enable='between(t,{offset:.3f},{game_end:.3f})'[v{index}]"
            )
        else:
            filters.append(f"[{index}:v]{video_normalize}[v{index}]")
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

        for card in cards_by_index.get(index, []):
            label = f"card{card_counter}"
            card_counter += 1
            card_duration = float(card["duration"])
            image_index = next_free_input
            next_free_input += 1
            command += ["-loop", "1", "-framerate", str(fps), "-i", str(card_image_path)]
            filters.append(
                f"[{image_index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={fps},setsar=1,format=yuv420p,"
                f"trim=duration={card_duration:.3f},setpts=PTS-STARTPTS[v{label}]"
            )
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={card_duration:.3f},"
                f"asetpts=PTS-STARTPTS[a{label}]"
            )
            concat_inputs.append(f"[v{label}][a{label}]")

    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(selections) + len(cards)}:v=1:a=1[vcat][acat]"
    )

    video_map = "[vcat]"
    if burn_in_captions is not None:
        if not burn_in_captions.is_file():
            raise FileNotFoundError(f"Caption file missing: {burn_in_captions}")
        escaped = ffmpeg_subtitles_path(burn_in_captions)
        filters.append(f"[vcat]ass='{escaped}'[vcap]")
        video_map = "[vcap]"

    dialogue_label = "[acat]"
    next_input = len(selections) + gameplay_input_count
    if cue_paths:
        sfx_labels: list[str] = []
        for offset, cue in enumerate(cues):
            input_index = next_input + offset
            delay_ms = max(0, round(shift_time(float(cue["at_sec"]), cards) * 1000))
            gain = float(cue.get("gain", 0.5))
            label = f"sfx{offset}"
            filters.append(
                f"[{input_index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={gain:.3f},"
                f"adelay={delay_ms}|{delay_ms}[{label}]"
            )
            sfx_labels.append(f"[{label}]")
        if len(sfx_labels) == 1:
            filters.append(
                f"{sfx_labels[0]}apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                "asetpts=PTS-STARTPTS[sfxmix]"
            )
        else:
            filters.append(
                "".join(sfx_labels)
                + f"amix=inputs={len(sfx_labels)}:duration=longest:normalize=0,"
                f"apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                "asetpts=PTS-STARTPTS[sfxmix]"
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
                input_index = next_input + bgm_segment_input_indexes[offset]
                start = shift_time(float(segment["start_sec"]), cards)
                end = shift_time(float(segment["end_sec"]), cards)
                seg_dur = max(0.05, end - start)
                fade = min(0.6, seg_dur / 3.0)
                fade_out = max(0.0, seg_dur - fade)
                delay_ms = max(0, round(start * 1000))
                volume = float(segment.get("volume", bgm_volume))
                label = f"bed{offset}"
                # Delay only — pad once after amix so 25 beds don't each hold a
                # full-timeline float buffer (that blew multi-GB RAM on long edits).
                filters.append(
                    f"[{input_index}:a]aresample=48000,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"atrim=duration={seg_dur:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fade:.3f},"
                    f"afade=t=out:st={fade_out:.3f}:d={fade:.3f},"
                    f"volume={volume:.3f},"
                    f"adelay={delay_ms}|{delay_ms}[{label}]"
                )
                bed_labels.append(f"[{label}]")
            if len(bed_labels) == 1:
                filters.append(
                    f"{bed_labels[0]}apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                    "asetpts=PTS-STARTPTS[music]"
                )
            else:
                filters.append(
                    "".join(bed_labels)
                    + f"amix=inputs={len(bed_labels)}:duration=longest:normalize=0,"
                    f"apad=whole_dur={total:.3f},atrim=duration={total:.3f},"
                    "asetpts=PTS-STARTPTS[music]"
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
                    # Duck fully under speech. makeup must stay 1: any makeup gain is
                    # applied to the whole bed, which re-raises music above dialogue and
                    # cancels the ducking it is supposed to help.
                    "[music][sidechain]sidechaincompress=threshold=0.03:ratio=8:"
                    "attack=15:release=450:makeup=1:mix=1[ducked]"
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
        video_map,
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
    return command, ";\n".join(filters), total


def verify_output(
    path: Path, expected_duration: float, fps: int, *, segment_count: int = 1
) -> dict[str, Any]:
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
    # Each concatenated segment (clip or time-skip card) can shed roughly a
    # fraction of a frame at its fps-conversion boundary — so the tolerance
    # scales with segment count rather than staying fixed regardless of how
    # many pieces went into the render.
    frame_tolerance = max(3, round(fps * 0.1), segment_count)
    if frames and abs(frames - expected_frames) > frame_tolerance:
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

    transitions = transitions_config(episode.config)
    time_skip_cards: list[dict[str, Any]] = []
    if transitions.get("enabled", True):
        time_skip_cards = plan_time_skip_cards(
            plan,
            interval_sec=float(transitions.get("interval_sec", 300.0)),
            card_duration=float(transitions.get("card_duration", 2.0)),
        )
        if time_skip_cards:
            print(f"Time-skip cards: {len(time_skip_cards)} inserted (~every "
                  f"{float(transitions.get('interval_sec', 300.0)) / 60:.0f} min)")

    burn_in_path: Path | None = None
    caption_meta: dict[str, Any] = {"enabled": False, "cues": 0}
    if episode.analysis_path.is_file():
        analysis = read_json(episode.analysis_path)
        caption_meta = prepare_caption_files(
            episode, plan, analysis, time_skip_cards=time_skip_cards
        )
        if caption_meta.get("enabled") and caption_meta.get("burn_in"):
            burn_in_path = Path(str(caption_meta["ass_path"]))
        if caption_meta.get("enabled"):
            print(
                f"Captions: {caption_meta.get('cues', 0)} cues"
                f"{' (burn-in)' if burn_in_path else ' (soft SRT only)'}"
            )
    elif bool((episode.config.get("captions") or {}).get("enabled", True)):
        print("Captions skipped: missing clip analysis (run `ve analyze` first).")

    command, filter_script, output_duration = build_render_command(
        episode,
        plan,
        output,
        burn_in_captions=burn_in_path,
        time_skip_cards=time_skip_cards,
    )
    filter_path = episode.work / "render_filter.txt"
    filter_path.write_text(filter_script, encoding="utf-8")
    print(f"Rendering {output_duration:.1f}s with {command[command.index('-c:v') + 1]}...")
    run(command)
    selection_count = sum(len(section.get("clips", []) or []) for section in plan.get("structure", []) or [])
    report = verify_output(
        output,
        output_duration,
        int(episode.config["output"]["fps"]),
        segment_count=selection_count + len(time_skip_cards),
    )
    report["captions"] = {
        "enabled": bool(caption_meta.get("enabled")),
        "burn_in": bool(burn_in_path),
        "cues": int(caption_meta.get("cues") or 0),
        "srt": caption_meta.get("srt_path"),
    }
    report["time_skip_cards"] = [
        {"label": card["label"], "content_time": round(float(card["content_time"]), 3)}
        for card in time_skip_cards
    ]
    write_json(episode.output / "verification.json", report)
    print(f"Wrote {output}")
    if caption_meta.get("srt_path"):
        print(f"Wrote {caption_meta['srt_path']}")
    return output
