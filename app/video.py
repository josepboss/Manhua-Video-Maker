import subprocess
import logging
import os
import json
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", audio_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 0))
    return 0.0


def create_video(
    panels: List[Tuple[str, str]],
    audio_path: str,
    output_path: str,
    job_id: str,
    settings: dict,
    progress_callback=None
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    video_format = settings.get("video_format", "vertical")
    watermark = settings.get("channel_watermark", "ManhuaRecap")

    if video_format == "landscape":
        target_w, target_h = 1920, 1080
    else:
        target_w, target_h = 1080, 1920

    selected_panels = [(img, text) for img, text in panels if text and text.strip()]
    if not selected_panels:
        selected_panels = panels[:min(len(panels), 20)]

    if not selected_panels:
        raise ValueError("No panels available for video assembly")

    audio_duration = get_audio_duration(audio_path)
    if audio_duration <= 0:
        audio_duration = 300.0

    duration_per_panel = audio_duration / len(selected_panels)
    duration_per_panel = max(duration_per_panel, 2.0)

    if progress_callback:
        progress_callback(80, "Assembling video clips...")

    concat_list_path = str(Path(output_path).parent / "concat_list.txt")
    clip_paths = []

    for i, (img_path, _) in enumerate(selected_panels):
        clip_path = str(Path(output_path).parent / f"clip_{i:04d}.mp4")
        clip_paths.append(clip_path)

        d = int(duration_per_panel * 25)
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"zoompan=z='min(zoom+0.0015,1.5)'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={d}"
            f":s={target_w}x{target_h}"
            f":fps=25"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", img_path,
            "-vf", vf,
            "-t", str(duration_per_panel),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            clip_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"FFmpeg clip {i} failed: {result.stderr}")
            raise RuntimeError(f"FFmpeg failed creating clip {i}: {result.stderr[-500:]}")

    with open(concat_list_path, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    if progress_callback:
        progress_callback(90, "Concatenating clips and adding audio...")

    watermark_safe = watermark.replace("'", "")
    drawtext = (
        f"drawtext=text='{watermark_safe}'"
        f":fontcolor=white@0.6"
        f":fontsize=36"
        f":x=(w-text_w)/2"
        f":y=h-60"
        f":shadowcolor=black@0.5"
        f":shadowx=2:shadowy=2"
    )

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-i", audio_path,
        "-vf", drawtext,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path
    ]
    result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"FFmpeg concat failed: {result.stderr}")
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")

    for cp in clip_paths:
        try:
            os.unlink(cp)
        except Exception:
            pass
    try:
        os.unlink(concat_list_path)
    except Exception:
        pass

    return output_path


