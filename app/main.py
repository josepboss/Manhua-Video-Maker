import os
import re
import uuid
import json
import shutil
import logging
import asyncio
import psutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings, save_settings
from app import scraper as scraper_mod
from app import memory as memory_mod
from app import auth as auth_mod

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

cancelled_jobs: set = set()
ffmpeg_processes: dict = {}


def is_cancelled(job_id: str) -> bool:
    return job_id in cancelled_jobs


SESSION_SECRET = os.environ.get("SESSION_SECRET", "manhua-dev-secret-change-me")

app = FastAPI(title="ManhuaRecap")

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="mr_session", max_age=60 * 60 * 24 * 30)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache"
}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_session_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return auth_mod.get_user_by_id(user_id)


def require_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ── Job helpers ───────────────────────────────────────────────────────────────

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


def update_job_stats(job_id: str, **stat_fields) -> None:
    job = read_job(job_id)
    job.setdefault("stats", {})
    job["stats"].update(stat_fields)
    write_job(job)


def assert_job_owner(job: dict, user: dict):
    job_uid = job.get("user_id")
    if job_uid and job_uid != user["user_id"] and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Access denied")


# ── Static / root ─────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(
            str(index_path),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                     "Pragma": "no-cache", "Expires": "0"}
        )
    return JSONResponse({"error": "index.html not found"}, status_code=404)


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/auth/register")
async def auth_register(request: Request, body: dict):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    try:
        user = auth_mod.create_user(username, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.session["user_id"] = user["user_id"]
    return JSONResponse(content={"user": user}, headers=NO_CACHE_HEADERS)


@app.post("/auth/login")
async def auth_login(request: Request, body: dict):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = auth_mod.authenticate(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = user["user_id"]
    return JSONResponse(content={"user": user}, headers=NO_CACHE_HEADERS)


@app.post("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return JSONResponse(content={"success": True}, headers=NO_CACHE_HEADERS)


@app.get("/auth/me")
async def auth_me(request: Request):
    user = get_session_user(request)
    if not user:
        return JSONResponse(content={"user": None}, headers=NO_CACHE_HEADERS)
    return JSONResponse(content={"user": user}, headers=NO_CACHE_HEADERS)


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings(request: Request):
    require_user(request)
    return JSONResponse(content=get_settings(), headers=NO_CACHE_HEADERS)


@app.post("/api/settings")
async def api_save_settings(request: Request, body: dict):
    require_admin(request)
    try:
        strip_fields = [
            "openrouter_api_key", "elevenlabs_api_key", "elevenlabs_voice_id",
            "azure_tts_key", "azure_tts_region", "azure_voice_name",
            "openai_tts_key", "openai_tts_voice", "watermark_text",
        ]
        for field in strip_fields:
            if field in body and isinstance(body[field], str):
                body[field] = body[field].strip()
        save_settings(body)
        return JSONResponse(content={"success": True}, headers=NO_CACHE_HEADERS)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Upload & process ──────────────────────────────────────────────────────────

@app.post("/api/upload")
async def api_upload(request: Request, files: list[UploadFile] = File(...)):
    user = require_user(request)
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
        "user_id": user["user_id"],
        "username": user["username"],
        "status": "queued",
        "progress": 0,
        "current_step": "Uploaded",
        "error_message": None,
        "created_at": datetime.utcnow().isoformat(),
        "upload_paths": saved_paths,
        "stats": {
            "panels_count": 0, "tokens_used": 0, "tts_chars": 0,
            "estimated_cost": 0.0, "llm_cost": 0.0, "tts_cost": 0.0
        }
    }
    write_job(job)
    return {"job_id": job_id, "files": [Path(p).name for p in saved_paths]}


@app.post("/api/process/{job_id}")
async def api_process(request: Request, job_id: str, background_tasks: BackgroundTasks, body: dict = None):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    if job["status"] not in ["queued", "failed"]:
        raise HTTPException(status_code=400, detail="Job already processing or complete")
    story_context = (body or {}).get("story_context", "")
    manga_title = (body or {}).get("manga_title", "")
    chapter_number = int((body or {}).get("chapter_number", 1) or 1)
    update_job(
        job_id,
        status="processing",
        progress=5,
        current_step="Starting pipeline...",
        story_context=story_context,
        manga_title=manga_title,
        chapter_number=chapter_number
    )
    background_tasks.add_task(run_pipeline, job_id)
    return {"job_id": job_id, "status": "processing"}


@app.get("/api/status/{job_id}")
async def api_status(request: Request, job_id: str):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    return job


@app.get("/api/download/{job_id}")
async def api_download_video(request: Request, job_id: str):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not complete")
    video_path = OUTPUT_DIR / job_id / "final.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(str(video_path), media_type="video/mp4",
                        filename=f"manhuarecap_{job_id[:8]}.mp4")


@app.get("/api/download/{job_id}/srt")
async def api_download_srt(request: Request, job_id: str):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not complete")
    srt_path = OUTPUT_DIR / job_id / "subtitles.srt"
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail="SRT file not found")
    return FileResponse(str(srt_path), media_type="text/plain",
                        filename=f"manhuarecap_{job_id[:8]}.srt")


@app.get("/api/debug/{job_id}")
async def api_debug_job(request: Request, job_id: str):
    require_admin(request)
    from app.ocr import extract_text
    panels_dir = PANELS_DIR / job_id
    if not panels_dir.exists():
        return {"error": f"No panels directory found for job {job_id}", "ocr_results": []}
    ocr_results = []
    for fname in sorted(panels_dir.iterdir()):
        if fname.suffix.lower() == ".png":
            text = extract_text(str(fname))
            ocr_results.append({"panel": fname.name, "text": text or "(empty)"})
    return {"job_id": job_id, "panel_count": len(ocr_results), "ocr_results": ocr_results}


@app.post("/api/scraper/fetch")
async def scraper_fetch(request: Request, body: dict):
    user = require_user(request)
    url = body.get("url", "").strip()
    selector = body.get("selector", "").strip()
    if not url:
        return {"success": False, "error": "URL is required"}
    result = scraper_mod.fetch_chapter(url, selector)
    if result["success"]:
        job_id = result["job_id"]
        job = {
            "job_id": job_id,
            "user_id": user["user_id"],
            "username": user["username"],
            "status": "queued",
            "progress": 0,
            "current_step": "Images scraped and ready",
            "error_message": None,
            "created_at": datetime.utcnow().isoformat(),
            "upload_paths": result["image_paths"],
            "source_url": url,
            "stats": {
                "panels_count": 0, "tokens_used": 0, "tts_chars": 0,
                "estimated_cost": 0.0, "llm_cost": 0.0, "tts_cost": 0.0
            }
        }
        write_job(job)
    return result


@app.post("/api/cancel/{job_id}")
async def cancel_job(request: Request, job_id: str):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    cancelled_jobs.add(job_id)
    if job_id in ffmpeg_processes:
        proc = ffmpeg_processes[job_id]
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
        except Exception as e:
            logger.warning(f"FFmpeg kill error for {job_id}: {e}")
        ffmpeg_processes.pop(job_id, None)
    try:
        update_job(job_id, status="cancelled", current_step="Cancelled by user")
    except Exception:
        pass
    return {"success": True}


@app.post("/api/retry/{job_id}")
async def api_retry(request: Request, job_id: str, background_tasks: BackgroundTasks):
    user = require_user(request)
    job = read_job(job_id)
    assert_job_owner(job, user)
    if job["status"] not in ["failed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Job is not in a failed or cancelled state")
    cancelled_jobs.discard(job_id)
    update_job(job_id, status="processing", progress=5,
               current_step="Retrying from last checkpoint...", error_message=None)
    background_tasks.add_task(run_pipeline, job_id)
    return {"job_id": job_id, "status": "processing"}


# ── Memory ────────────────────────────────────────────────────────────────────

@app.get("/api/memory")
async def list_all_memory(request: Request):
    user = require_user(request)
    return {"memories": memory_mod.list_memories(user["user_id"])}


@app.get("/api/memory/{manga_title}")
async def get_manga_memory(request: Request, manga_title: str):
    user = require_user(request)
    mem = memory_mod.load_memory(manga_title, user["user_id"])
    chapters = mem.get("chapters", {})
    return {
        "manga_title": manga_title,
        "chapters_count": len(chapters),
        "latest_chapter": max([int(k) for k in chapters.keys()], default=0),
        "chapters": chapters
    }


@app.delete("/api/memory/{manga_title}")
async def delete_manga_memory(request: Request, manga_title: str):
    user = require_user(request)
    memory_mod.delete_memory(manga_title, user["user_id"])
    return {"success": True}


# ── Jobs list ─────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
async def api_list_jobs(request: Request):
    user = require_user(request)
    jobs = []
    for jf in sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        with open(jf) as f:
            job = json.load(f)
        if user.get("is_admin") or job.get("user_id") == user["user_id"] or not job.get("user_id"):
            jobs.append(job)
    return {"jobs": jobs}


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    require_admin(request)
    users = auth_mod.get_all_users()
    for u in users:
        uid = u["user_id"]
        job_files = list(JOBS_DIR.glob("*.json"))
        user_jobs = []
        for jf in job_files:
            with open(jf) as f:
                try:
                    j = json.load(f)
                except Exception:
                    continue
            if j.get("user_id") == uid:
                user_jobs.append(j)
        u["jobs_total"] = len(user_jobs)
        u["jobs_complete"] = sum(1 for j in user_jobs if j.get("status") == "complete")
        u["jobs_failed"] = sum(1 for j in user_jobs if j.get("status") == "failed")
        total_cost = sum(j.get("stats", {}).get("estimated_cost", 0) for j in user_jobs)
        u["total_cost"] = round(total_cost, 4)
    return {"users": users}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(request: Request, user_id: str):
    admin = require_admin(request)
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    auth_mod.delete_user(user_id)
    return {"success": True}


@app.post("/api/admin/users/{user_id}/toggle-admin")
async def admin_toggle_admin(request: Request, user_id: str):
    admin = require_admin(request)
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot change your own admin status")
    target = auth_mod.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    auth_mod.set_admin(user_id, not target.get("is_admin", False))
    return {"success": True, "is_admin": not target.get("is_admin", False)}


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    require_admin(request)
    all_jobs = []
    for jf in JOBS_DIR.glob("*.json"):
        with open(jf) as f:
            try:
                all_jobs.append(json.load(f))
            except Exception:
                pass
    users = auth_mod.get_all_users()
    return {
        "total_users": len(users),
        "total_jobs": len(all_jobs),
        "jobs_complete": sum(1 for j in all_jobs if j.get("status") == "complete"),
        "jobs_failed": sum(1 for j in all_jobs if j.get("status") == "failed"),
        "jobs_processing": sum(1 for j in all_jobs if j.get("status") == "processing"),
        "total_cost": round(sum(j.get("stats", {}).get("estimated_cost", 0) for j in all_jobs), 4),
        "total_panels": sum(j.get("stats", {}).get("panels_count", 0) for j in all_jobs),
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def run_pipeline(job_id: str):
    try:
        await asyncio.to_thread(_run_pipeline_sync, job_id)
    except Exception as e:
        logger.error(f"Pipeline error for {job_id}: {e}")
        try:
            update_job(job_id, status="failed", error_message=str(e))
        except Exception:
            pass
    finally:
        cancelled_jobs.discard(job_id)
        ffmpeg_processes.pop(job_id, None)


def _run_pipeline_sync(job_id: str):
    os.setpgrp()

    from app import panels as panels_mod
    from app import ocr as ocr_mod
    from app import script as script_mod
    from app import tts as tts_mod
    from app import video as video_mod

    settings = get_settings()
    job = read_job(job_id)
    upload_paths = job.get("upload_paths", [])
    user_id = job.get("user_id", "shared")

    def progress(pct: int, step: str):
        update_job(job_id, progress=pct, current_step=step)

    def natural_sort_key(path):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", os.path.basename(path))]

    ocr_lang = settings.get("ocr_language", "en")
    narration_lang = settings.get("narration_language", "English")

    # ── Stage 1: Panel detection ──────────────────────────────────────────────
    if "panel_paths" not in job:
        progress(10, "Detecting panels...")
        image_paths = []
        for path in upload_paths:
            if path.lower().endswith(".pdf"):
                pdf_upload_dir = str(UPLOADS_DIR / job_id)
                imgs = panels_mod.convert_pdf_to_images(path, pdf_upload_dir)
                image_paths.extend(imgs)
            else:
                image_paths.append(path)
        image_paths.sort(key=natural_sort_key)
        panels_out_dir = str(PANELS_DIR / job_id)
        panel_paths = panels_mod.process_images_to_panels(image_paths, panels_out_dir, job_id)
        update_job_stats(job_id, panels_count=len(panel_paths))
        update_job(job_id, panel_paths=panel_paths)
        progress(25, f"Detected {len(panel_paths)} panels. Extracting text...")
    else:
        panel_paths = job["panel_paths"]
        progress(25, f"Resuming — {len(panel_paths)} panels already detected")

    if is_cancelled(job_id):
        update_job(job_id, status="cancelled", current_step="Cancelled by user")
        return

    # ── Stage 2: OCR ─────────────────────────────────────────────────────────
    if "ocr_results" not in job:
        panel_data = []
        for i, panel_path in enumerate(panel_paths):
            text = ocr_mod.extract_text(panel_path, lang=ocr_lang)
            if not text:
                logger.info(f"Panel {i} returned empty OCR, skipping in narration")
            panel_data.append((panel_path, text))
            if i % 5 == 0:
                pct = 25 + int((i / len(panel_paths)) * 15)
                progress(pct, f"OCR: {i+1}/{len(panel_paths)} panels...")
        ocr_results = [{"path": p, "text": t or ""} for p, t in panel_data]
        update_job(job_id, ocr_results=ocr_results)
    else:
        panel_data = [(r["path"], r["text"]) for r in job["ocr_results"]]
        progress(40, f"Resuming — OCR already done ({len(panel_data)} panels)")

    if is_cancelled(job_id):
        update_job(job_id, status="cancelled", current_step="Cancelled by user")
        return

    # ── Stage 3: Script generation ────────────────────────────────────────────
    if "final_script" not in job:
        progress(40, "Writing narration script...")
        openrouter_key = settings.get("openrouter_api_key", "")
        if not openrouter_key:
            raise ValueError("OpenRouter API key not configured in Settings")
        openrouter_model = settings.get("openrouter_model", "openai/gpt-4o-mini")
        story_context = job.get("story_context", "")
        manga_title = job.get("manga_title", "")
        chapter_number = int(job.get("chapter_number", 1) or 1)

        if manga_title and chapter_number > 1:
            mem_ctx = memory_mod.get_context_for_chapter(manga_title, chapter_number, user_id)
            if mem_ctx:
                story_context = mem_ctx + ("\n\n" + story_context if story_context else "")

        def script_progress(pct: int, step: str):
            progress(40 + pct, step)

        final_script, srt_content, llm_stats = script_mod.generate_script(
            panel_data,
            openrouter_key,
            openrouter_model,
            narration_language=narration_lang,
            ocr_lang=ocr_lang,
            story_context=story_context,
            manga_title=manga_title,
            chapter_number=chapter_number,
            progress_callback=script_progress
        )
        total_tokens = llm_stats.get("tokens_used", 0)
        llm_cost = round(total_tokens / 1_000_000 * 0.15, 6)
        update_job_stats(job_id, tokens_used=total_tokens, llm_cost=llm_cost)
        update_job(job_id, final_script=final_script, srt_content=srt_content, llm_stats=llm_stats)

        if manga_title and chapter_number:
            memory_mod.save_chapter_memory(manga_title, chapter_number, final_script,
                                           openrouter_key, openrouter_model, user_id)
    else:
        final_script = job["final_script"]
        srt_content = job.get("srt_content", "")
        llm_stats = job.get("llm_stats", {})
        total_tokens = llm_stats.get("tokens_used", 0)
        llm_cost = round(total_tokens / 1_000_000 * 0.15, 6)
        progress(80, "Resuming — script already generated")

    if is_cancelled(job_id):
        update_job(job_id, status="cancelled", current_step="Cancelled by user")
        return

    # ── Stage 4: TTS ──────────────────────────────────────────────────────────
    if "audio_path" not in job:
        progress(80, "Generating audio narration...")
        audio_out_dir = str(AUDIO_DIR / job_id)
        audio_path, tts_chars, tts_cost = tts_mod.generate_audio(
            final_script, job_id, settings, audio_out_dir
        )
        update_job_stats(job_id, tts_chars=tts_chars, tts_cost=tts_cost)
        update_job(job_id, audio_path=audio_path)
    else:
        audio_path = job["audio_path"]
        progress(85, "Resuming — audio already generated")

    if is_cancelled(job_id):
        update_job(job_id, status="cancelled", current_step="Cancelled by user")
        return

    # ── Stage 5: Video assembly ───────────────────────────────────────────────
    progress(85, "Assembling video...")
    output_out_dir = OUTPUT_DIR / job_id
    output_out_dir.mkdir(parents=True, exist_ok=True)
    video_output = str(output_out_dir / "final.mp4")
    srt_output = str(output_out_dir / "subtitles.srt")

    with open(srt_output, "w") as f:
        f.write(srt_content)

    def video_progress(pct: int, step: str):
        progress(85 + int(pct * 0.14), step)

    video_mod.create_video(
        panel_data, audio_path, video_output, job_id, settings,
        progress_callback=video_progress, process_registry=ffmpeg_processes
    )

    # ── Final stats ───────────────────────────────────────────────────────────
    tts_chars_final = len(final_script)
    estimated_cost = round(
        (total_tokens / 1_000_000 * 0.15) + (tts_chars_final / 1_000_000 * 15), 6
    )

    update_job_stats(
        job_id,
        panels_count=llm_stats.get("panels_count", len(panel_data)),
        tokens_used=total_tokens, tts_chars=tts_chars_final,
        estimated_cost=estimated_cost, llm_cost=llm_cost, tts_cost=tts_cost
    )
    update_job(job_id, status="complete", progress=100, current_step="Complete!")
    _cleanup_job(job_id)


def _cleanup_job(job_id: str):
    for d, label in [(UPLOADS_DIR / job_id, "uploads"), (PANELS_DIR / job_id, "panels")]:
        try:
            if d.exists():
                shutil.rmtree(str(d))
        except Exception as e:
            logger.warning(f"Cleanup {label} failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
