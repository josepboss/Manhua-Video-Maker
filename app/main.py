import os
import uuid
import json
import shutil
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings, save_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
PANELS_DIR = BASE_DIR / "panels"
AUDIO_DIR = BASE_DIR / "audio"
OUTPUT_DIR = BASE_DIR / "output"
JOBS_DIR = BASE_DIR / "jobs"

for d in [UPLOADS_DIR, PANELS_DIR, AUDIO_DIR, OUTPUT_DIR, JOBS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ManhuaRecap")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def read_job(job_id: str) -> dict:
    path = get_job_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    with open(path) as f:
        return json.load(f)


def write_job(job: dict) -> None:
    path = get_job_path(job["job_id"])
    with open(path, "w") as f:
        json.dump(job, f, indent=2)


def update_job(job_id: str, **kwargs) -> None:
    job = read_job(job_id)
    job.update(kwargs)
    write_job(job)


@app.get("/")
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"error": "index.html not found"}, status_code=404)


@app.get("/api/settings")
async def api_get_settings():
    return get_settings()


@app.post("/api/settings")
async def api_save_settings(body: dict):
    try:
        save_settings(body)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def api_upload(files: list[UploadFile] = File(...)):
    job_id = str(uuid.uuid4())
    job_upload_dir = UPLOADS_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for f in files:
        safe_name = f.filename.replace("/", "_").replace("\\", "_")
        dest = job_upload_dir / safe_name
        content = await f.read()
        with open(dest, "wb") as out:
            out.write(content)
        saved_paths.append(str(dest))

    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "current_step": "Uploaded",
        "error_message": None,
        "created_at": datetime.utcnow().isoformat(),
        "upload_paths": saved_paths,
        "stats": {
            "panels_count": 0,
            "tokens_used": 0,
            "tts_chars": 0,
            "estimated_cost": 0.0
        }
    }
    write_job(job)
    return {"job_id": job_id, "files": [Path(p).name for p in saved_paths]}


@app.post("/api/process/{job_id}")
async def api_process(job_id: str, background_tasks: BackgroundTasks):
    job = read_job(job_id)
    if job["status"] not in ["queued", "failed"]:
        raise HTTPException(status_code=400, detail="Job already processing or complete")
    update_job(job_id, status="processing", progress=5, current_step="Starting pipeline...")
    background_tasks.add_task(run_pipeline, job_id)
    return {"job_id": job_id, "status": "processing"}


@app.get("/api/status/{job_id}")
async def api_status(job_id: str):
    return read_job(job_id)


@app.get("/api/download/{job_id}")
async def api_download_video(job_id: str):
    job = read_job(job_id)
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not complete")
    video_path = OUTPUT_DIR / job_id / "final.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(
        str(video_path),
        media_type="video/mp4",
        filename=f"manhuarecap_{job_id[:8]}.mp4"
    )


@app.get("/api/download/{job_id}/srt")
async def api_download_srt(job_id: str):
    job = read_job(job_id)
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not complete")
    srt_path = OUTPUT_DIR / job_id / "subtitles.srt"
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail="SRT file not found")
    return FileResponse(
        str(srt_path),
        media_type="text/plain",
        filename=f"manhuarecap_{job_id[:8]}.srt"
    )


@app.get("/api/jobs")
async def api_list_jobs():
    jobs = []
    for jf in sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        with open(jf) as f:
            jobs.append(json.load(f))
    return {"jobs": jobs}


async def run_pipeline(job_id: str):
    try:
        await asyncio.to_thread(_run_pipeline_sync, job_id)
    except Exception as e:
        logger.error(f"Pipeline error for {job_id}: {e}")
        try:
            update_job(job_id, status="failed", error_message=str(e))
        except Exception:
            pass


def _run_pipeline_sync(job_id: str):
    from app import panels as panels_mod
    from app import ocr as ocr_mod
    from app import script as script_mod
    from app import tts as tts_mod
    from app import video as video_mod

    settings = get_settings()
    job = read_job(job_id)
    upload_paths = job.get("upload_paths", [])

    def progress(pct, step):
        update_job(job_id, progress=pct, current_step=step)

    progress(10, "Detecting panels...")

    image_paths = []
    for path in upload_paths:
        if path.lower().endswith(".pdf"):
            pdf_upload_dir = str(UPLOADS_DIR / job_id)
            imgs = panels_mod.convert_pdf_to_images(path, pdf_upload_dir)
            image_paths.extend(imgs)
        else:
            image_paths.append(path)

    panels_out_dir = str(PANELS_DIR / job_id)
    panel_paths = panels_mod.process_images_to_panels(image_paths, panels_out_dir, job_id)
    progress(25, f"Detected {len(panel_paths)} panels. Extracting text...")

    panel_data = []
    for i, panel_path in enumerate(panel_paths):
        text = ocr_mod.extract_text(panel_path)
        if not text:
            logger.info(f"Panel {i} returned empty OCR, skipping")
        panel_data.append((panel_path, text))
        if i % 5 == 0:
            progress(25 + int(i / len(panel_paths) * 15), f"OCR: {i+1}/{len(panel_paths)} panels...")

    progress(40, "Writing narration script...")

    openrouter_key = settings.get("openrouter_api_key", "")
    if not openrouter_key:
        raise ValueError("OpenRouter API key not configured in Settings")

    openrouter_model = settings.get("openrouter_model", "openai/gpt-4o-mini")

    def script_progress(pct, step):
        progress(40 + pct, step)

    final_script, srt_content, llm_stats = script_mod.generate_script(
        panel_data,
        openrouter_key,
        openrouter_model,
        progress_callback=script_progress
    )

    progress(80, "Generating audio narration...")

    audio_out_dir = str(AUDIO_DIR / job_id)
    audio_path, tts_chars, tts_cost = tts_mod.generate_audio(
        final_script, job_id, settings, audio_out_dir
    )

    progress(85, "Assembling video...")

    output_out_dir = OUTPUT_DIR / job_id
    output_out_dir.mkdir(parents=True, exist_ok=True)
    video_output = str(output_out_dir / "final.mp4")
    srt_output = str(output_out_dir / "subtitles.srt")

    with open(srt_output, "w") as f:
        f.write(srt_content)

    def video_progress(pct, step):
        progress(85 + int(pct * 0.14), step)

    video_mod.create_video(
        panel_data,
        audio_path,
        video_output,
        job_id,
        settings,
        progress_callback=video_progress
    )

    estimated_cost = round(llm_stats.get("estimated_llm_cost", 0) + tts_cost, 4)

    stats = {
        "panels_count": llm_stats.get("panels_count", len(panel_data)),
        "tokens_used": llm_stats.get("tokens_used", 0),
        "tts_chars": tts_chars,
        "estimated_cost": estimated_cost,
        "tts_cost": tts_cost,
        "llm_cost": llm_stats.get("estimated_llm_cost", 0)
    }

    update_job(job_id,
               status="complete",
               progress=100,
               current_step="Complete!",
               stats=stats)

    _cleanup_job(job_id, upload_paths)


def _cleanup_job(job_id: str, upload_paths: list):
    try:
        job_upload_dir = UPLOADS_DIR / job_id
        if job_upload_dir.exists():
            shutil.rmtree(str(job_upload_dir))
    except Exception as e:
        logger.warning(f"Cleanup uploads failed: {e}")
    try:
        panels_job_dir = PANELS_DIR / job_id
        if panels_job_dir.exists():
            shutil.rmtree(str(panels_job_dir))
    except Exception as e:
        logger.warning(f"Cleanup panels failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
