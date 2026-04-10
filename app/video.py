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


def make_panel_clip(panel_path: str, clip_path: str, duration: float, resolution: str = "landscape", on_popen=None) -> None:
    if resolution == "landscape":
        w, h = "1280", "720"
    else:
        w, h = "720", "1280"

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"zoompan=z='1.2-0.2*on/120'"
        f":x='(iw-iw/zoom)/2'"
        f":y='(ih-ih/zoom)/2'"
        f":d=120"
        f":s={w}x{h}"
        f":fps=15"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", panel_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "30",
        "-tune", "stillimage",
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        clip_path
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if on_popen:
        on_popen(proc)
    _, stderr = proc.communicate(timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg clip failed for {panel_path}: {stderr.decode()[-500:]}")


def create_video(
    panels: List[Tuple[str, str]],
    audio_path: str,
    output_path: str,
    job_id: str,
    settings: dict,
    progress_callback=None,
    process_registry: dict = None
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    def register(proc):
        if process_registry is not None:
            process_registry[job_id] = proc

    resolution = settings.get("video_format", "landscape")
    watermark_text = settings.get("watermark_text", "ManhuaRecap").replace("'", "")

    if resolution == "landscape":
        target_w, target_h = 1280, 720
    else:
        target_w, target_h = 720, 1280

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
        progress_callback(0, "Assembling video clips...")

    concat_list_path = str(Path(output_path).parent / "concat_list.txt")
    clip_paths = []

    for i, (img_path, _) in enumerate(selected_panels):
        clip_path = str(Path(output_path).parent / f"clip_{i:04d}.mp4")
        clip_paths.append(clip_path)

        try:
            make_panel_clip(img_path, clip_path, duration_per_panel, resolution, on_popen=register)
        except RuntimeError as e:
            logger.error(f"Clip {i} failed: {e}")
            raise

        if progress_callback:
            pct = int((i + 1) / len(selected_panels) * 80)
            progress_callback(pct, f"Encoding clip {i+1}/{len(selected_panels)}...")

    with open(concat_list_path, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    if progress_callback:
        progress_callback(85, "Concatenating clips and adding audio...")

    drawtext = (
        f"drawtext=text='{watermark_text}'"
        f":fontcolor=white"
        f":fontsize=24"
        f":alpha=0.6"
        f":x=(w-text_w)/2"
        f":y=h-50"
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
        "-preset", "ultrafast",
        "-crf", "28",
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        output_path
    ]
    concat_proc = subprocess.Popen(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    register(concat_proc)
    _, stderr = concat_proc.communicate(timeout=600)
    if process_registry is not None:
        process_registry.pop(job_id, None)
    if concat_proc.returncode != 0:
        logger.error(f"FFmpeg concat failed: {stderr.decode()}")
        raise RuntimeError(f"FFmpeg concat failed: {stderr.decode()[-500:]}")

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
