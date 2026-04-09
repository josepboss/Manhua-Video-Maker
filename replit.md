# Workspace

## Overview

pnpm workspace monorepo using TypeScript plus a Python FastAPI app.

## Projects

### ManhuaRecap (Primary App)
- **Type**: Python FastAPI web app
- **Location**: `/app/` directory (root of workspace)
- **Frontend**: Single-file HTML at `static/index.html` (served by FastAPI)
- **Running on**: Port 5000
- **Accessible at**: `/` (root preview path)

### TypeScript API Server (Legacy/Unused by ManhuaRecap)
- **Location**: `artifacts/api-server/`
- **Running on**: Port 8080 at path `/ts-api` (moved from `/api` to avoid conflict)

## ManhuaRecap Architecture

### Backend Modules
- `app/main.py` — FastAPI app, routes, job queue, background pipeline runner
- `app/config.py` — Load/save settings.json
- `app/panels.py` — Panel detection from images using OpenCV; PDF conversion using pdf2image
- `app/ocr.py` — PaddleOCR text extraction with text cleaning
- `app/script.py` — LLM narration via OpenRouter API; SRT generation
- `app/tts.py` — TTS synthesis (OpenAI, ElevenLabs, Azure)
- `app/video.py` — FFmpeg video assembly with Ken Burns effect
- `app/context.py` — Rolling narrative context buffer for LLM

### Settings
- `app/settings.json` — Persistent config (all API keys, TTS provider, video format)

### Directories (created at runtime)
- `app/uploads/` — Uploaded files per job
- `app/panels/` — Extracted panel images per job
- `app/audio/` — TTS audio per job
- `app/output/` — Final MP4 and SRT files per job
- `app/jobs/` — Job status JSON files

## Stack

### Python App
- **Framework**: FastAPI + uvicorn
- **OCR**: PaddleOCR (models download on first use)
- **Image**: OpenCV (headless), Pillow
- **PDF**: pdf2image + poppler
- **LLM**: OpenRouter API (configurable model)
- **TTS**: OpenAI / ElevenLabs / Azure TTS (user-selectable)
- **Video**: FFmpeg (system-installed via Nix)

### Monorepo (TypeScript)
- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9

## Key Commands

- `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload` — Run ManhuaRecap dev server

## API Routes

- `GET /` — Serve `static/index.html`
- `GET /api/settings` — Get current settings
- `POST /api/settings` — Save settings
- `POST /api/upload` — Upload images/PDF, returns job_id
- `POST /api/process/{job_id}` — Start background pipeline
- `GET /api/status/{job_id}` — Poll job status/progress
- `GET /api/download/{job_id}` — Download final MP4
- `GET /api/download/{job_id}/srt` — Download subtitle SRT
- `GET /api/jobs` — List all jobs
