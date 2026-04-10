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


def make_panel_clip(panel_path: str, clip_path: str, duration: float, resolution: str = "landscape") -> bool:
    if resolution == "landscape":
        w, h = 1280, 720
    else:
        w, h = 720, 1280

    logger.info(f"Starting clip: {panel_path} → {clip_path} ({duration:.2f}s)")

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", panel_path,
        "-vf", (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"zoompan=z='1.2-0.2*on/120'"
            f":x='(iw-iw/zoom)/2'"
            f":y='(ih-ih/zoom)/2'"
            f":d=120:s={w}x{h}:fps=15"
        ),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "30",
        "-threads", "1",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        clip_path
    ]

    timeout = max(90, int(duration * 5))
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            close_fds=True
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg failed (code {result.returncode})")
            return False
        if not os.path.exists(clip_path):
            logger.error(f"Clip not found after FFmpeg: {clip_path}")
            return False
        logger.info(f"Clip done: {clip_path}")
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg timeout on {panel_path}")
        return False
    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        return False


def create_video(
    panels: List[Tuple[str, str]],
    audio_path: str,
    output_path: str,
    job_id: str,
    settings: dict,
    panel_durations: List[float] = None,
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
        w, h = 1280, 720
    else:
        w, h = 720, 1280

    if panel_durations:
        n = min(len(panels), len(panel_durations))
        selected_panels = panels[:n]
        durations = panel_durations[:n]
        logger.info(
            f"Timed mode: {n} panels, "
            f"durations {min(durations):.1f}s–{max(durations):.1f}s"
        )
    else:
        selected_panels = [(img, text) for img, text in panels if text and text.strip()]
        if not selected_panels:
            selected_panels = panels[:min(len(panels), 20)]
        audio_duration = get_audio_duration(audio_path)
        if audio_duration <= 0:
            audio_duration = 300.0
        uniform = max(audio_duration / len(selected_panels), 2.0)
        durations = [uniform] * len(selected_panels)
        logger.info(f"Uniform mode: {len(selected_panels)} panels, {uniform:.2f}s each")

    if not selected_panels:
        raise ValueError("No panels available for video assembly")

    if progress_callback:
        progress_callback(0, "Encoding panel clips...")

    work_dir = Path(output_path).parent
    concat_list_path = str(work_dir / "concat_list.txt")
    temp_video_path = str(work_dir / "temp_video.mp4")
    clip_paths = []

    # ── Step 1: encode each panel as a silent video clip ─────────────────────
    for i, (img_path, _) in enumerate(selected_panels):
        clip_path = str(work_dir / f"clip_{i:04d}.mp4")
        logger.info(f"Panel {i+1}/{len(selected_panels)}: {img_path}")

        if make_panel_clip(img_path, clip_path, durations[i], resolution):
            clip_paths.append(clip_path)
        else:
            logger.warning(f"Skipping failed clip {i+1}")

        if progress_callback:
            pct = int((i + 1) / len(selected_panels) * 75)
            progress_callback(pct, f"Encoding clip {i+1}/{len(selected_panels)}...")

    if not clip_paths:
        raise RuntimeError("All panel clips failed — nothing to concatenate")

    logger.info(f"Clips done: {len(clip_paths)}/{len(selected_panels)}")

    # ── Step 2: concatenate clips into silent temp video ──────────────────────
    if progress_callback:
        progress_callback(78, "Concatenating clips...")

    with open(concat_list_path, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    concat_result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            temp_video_path
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=300,
        close_fds=True
    )
    if concat_result.returncode != 0:
        raise RuntimeError("FFmpeg concat of panel clips failed")

    # ── Step 3: mux temp video + narration audio → final with watermark ───────
    if progress_callback:
        progress_callback(85, "Muxing audio and adding watermark...")

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

    mux_cmd = [
        "ffmpeg", "-y",
        "-i", temp_video_path,
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

    mux_proc = subprocess.Popen(
        mux_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        close_fds=True
    )
    register(mux_proc)
    _, stderr = mux_proc.communicate(timeout=600)
    if process_registry is not None:
        process_registry.pop(job_id, None)

    if mux_proc.returncode != 0:
        logger.error(f"FFmpeg mux failed: {stderr.decode()[-500:]}")
        raise RuntimeError(f"FFmpeg mux failed: {stderr.decode()[-500:]}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    for cp in clip_paths:
        try:
            os.unlink(cp)
        except Exception:
            pass
    for tmp in [concat_list_path, temp_video_path]:
        try:
            os.unlink(tmp)
        except Exception:
            pass

    return output_path
