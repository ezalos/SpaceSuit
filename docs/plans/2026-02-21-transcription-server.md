# Transcription Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a FastAPI server that accepts audio file uploads and returns plain text transcripts using whisperx with GPU acceleration.

**Architecture:** Single FastAPI app in `transcription_server/`. Loads whisperx large-v2 model at startup, keeps it in GPU VRAM. One POST endpoint `/transcribe` accepts audio uploads, serializes GPU access via `asyncio.Lock`, returns plain text. Optional diarization via query param.

**Tech Stack:** Python, FastAPI, uvicorn, whisperx, torch, python-multipart

---

### Task 1: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add server dependencies**

Add `fastapi`, `uvicorn`, `python-multipart`, and `whisperx` to the `[project.dependencies]` list in `pyproject.toml`. Also add an optional `[project.optional-dependencies]` group for the server:

```toml
[project.optional-dependencies]
server = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "python-multipart>=0.0.18",
    "whisperx @ git+https://github.com/m-bain/whisperX.git",
]
```

**Step 2: Sync dependencies**

Run: `uv sync --extra server`
Expected: Dependencies install successfully. whisperx pulls in torch, faster-whisper, pyannote, etc.

**Step 3: Verify import works**

Run: `uv run python -c "import whisperx; import fastapi; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add transcription server dependencies (fastapi, whisperx)"
```

---

### Task 2: Create the server module with health endpoint

**Files:**
- Create: `transcription_server/__init__.py`
- Create: `transcription_server/server.py`

**Step 1: Create `transcription_server/__init__.py`**

```python
# ABOUTME: Transcription server package - FastAPI wrapper around whisperx.
# ABOUTME: Accepts audio uploads and returns plain text transcripts.
```

**Step 2: Create `transcription_server/server.py` with health endpoint**

```python
# ABOUTME: FastAPI server that wraps whisperx for audio transcription.
# ABOUTME: Loads model at startup, accepts audio uploads, returns plain text.

import asyncio
import gc
import os
import tempfile
from contextlib import asynccontextmanager

import torch
import whisperx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

DEVICE = os.getenv("WHISPERX_DEVICE", "cuda")
MODEL_NAME = os.getenv("WHISPERX_MODEL", "large-v2")
COMPUTE_TYPE = os.getenv("WHISPERX_COMPUTE_TYPE", "float32")
BATCH_SIZE = int(os.getenv("WHISPERX_BATCH_SIZE", "16"))
HF_TOKEN = os.getenv("HF_TOKEN", "")

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "whisperx_uploads")

# Serialize all GPU work through a single lock
_gpu_lock = asyncio.Lock()


class TranscribeResponse(BaseModel):
    text: str
    language: str


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load whisperx model at startup, clean up on shutdown."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # Clean stale uploads from previous runs
    for f in os.listdir(UPLOAD_DIR):
        filepath = os.path.join(UPLOAD_DIR, f)
        if os.path.isfile(filepath):
            os.unlink(filepath)

    asr_options = {
        "beam_size": 10,
        "best_of": 10,
        "patience": 2,
    }
    app.state.model = whisperx.load_model(
        MODEL_NAME,
        DEVICE,
        compute_type=COMPUTE_TYPE,
        asr_options=asr_options,
    )
    app.state.model_loaded = True
    yield
    # Shutdown: release GPU memory
    del app.state.model
    app.state.model_loaded = False
    gc.collect()
    torch.cuda.empty_cache()


app = FastAPI(title="WhisperX Transcription Server", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        model_loaded=getattr(app.state, "model_loaded", False),
    )
```

**Step 3: Test the server starts and health works**

Run: `uv run uvicorn transcription_server.server:app --host 127.0.0.1 --port 8642 &`
Then: `curl http://127.0.0.1:8642/health`
Expected: `{"status":"ok","model_loaded":true}`
Then kill the server.

**Step 4: Commit**

```bash
git add transcription_server/
git commit -m "feat: transcription server skeleton with health endpoint"
```

---

### Task 3: Implement the /transcribe endpoint

**Files:**
- Modify: `transcription_server/server.py`

**Step 1: Add the transcription helper function**

Add this function after the health endpoint in `server.py`:

```python
def _do_transcribe(
    model,
    file_path: str,
    language: str | None,
    diarize: bool,
    min_speakers: int,
    max_speakers: int,
) -> dict:
    """Run whisperx transcription pipeline. Called in executor to avoid blocking."""
    audio = whisperx.load_audio(file_path)
    result = model.transcribe(audio, batch_size=BATCH_SIZE, language=language)
    detected_lang = result.get("language", language or "unknown")

    # Alignment for better word-level timestamps
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code=detected_lang, device=DEVICE
        )
        result = whisperx.align(
            result["segments"],
            model_a,
            metadata,
            audio,
            DEVICE,
            return_char_alignments=False,
        )
        del model_a, metadata
    except Exception:
        # Alignment may fail for unsupported languages; fall back to unaligned
        pass

    # Optional diarization
    if diarize and HF_TOKEN:
        try:
            from whisperx.diarize import DiarizationPipeline

            diarize_model = DiarizationPipeline(
                token=HF_TOKEN, device=DEVICE
            )
            diarize_segments = diarize_model(
                audio,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            result = whisperx.assign_word_speakers(diarize_segments, result)
            del diarize_model, diarize_segments
        except Exception:
            # Diarization can fail; fall back to undiarized result
            pass

    gc.collect()
    torch.cuda.empty_cache()

    # Build plain text from segments
    segments = result.get("segments", [])
    full_text = " ".join(seg.get("text", "").strip() for seg in segments)

    return {"text": full_text, "language": detected_lang}
```

**Step 2: Add the /transcribe endpoint**

```python
@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Query(
        default=None,
        description="Language code (e.g. 'fr', 'en'). Auto-detect if omitted.",
    ),
    diarize: bool = Query(
        default=False,
        description="Enable speaker diarization.",
    ),
    min_speakers: int = Query(
        default=2,
        description="Minimum number of speakers (only used if diarize=true).",
    ),
    max_speakers: int = Query(
        default=2,
        description="Maximum number of speakers (only used if diarize=true).",
    ),
):
    """Transcribe an uploaded audio file and return plain text."""
    # Save uploaded file to disk (whisperx needs a file path)
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp_path = os.path.join(UPLOAD_DIR, f"{os.urandom(8).hex()}{suffix}")
    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                f.write(chunk)

        # Serialize GPU access — only one transcription at a time
        async with _gpu_lock:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _do_transcribe(
                    app.state.model,
                    tmp_path,
                    language,
                    diarize,
                    min_speakers,
                    max_speakers,
                ),
            )
        return TranscribeResponse(**result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
```

**Step 3: Test with a real audio file**

Start server: `uv run uvicorn transcription_server.server:app --host 0.0.0.0 --port 8642`

Test with any audio file on disk:
```bash
curl -X POST "http://127.0.0.1:8642/transcribe?language=fr" \
  -F "file=@/path/to/some/audio.wav"
```
Expected: `{"text": "...", "language": "fr"}`

Test auto-detect:
```bash
curl -X POST "http://127.0.0.1:8642/transcribe" \
  -F "file=@/path/to/some/audio.wav"
```
Expected: `{"text": "...", "language": "fr"}` (or whatever language was detected)

Test health still works:
```bash
curl http://127.0.0.1:8642/health
```
Expected: `{"status":"ok","model_loaded":true}`

**Step 4: Commit**

```bash
git add transcription_server/server.py
git commit -m "feat: add /transcribe endpoint with language detection and diarization"
```

---

### Task 4: Add a launch script

**Files:**
- Create: `transcription_server/run.sh`

**Step 1: Create the launch script**

```bash
#!/usr/bin/env bash
# ABOUTME: Launch script for the whisperx transcription server.
# ABOUTME: Starts uvicorn on 0.0.0.0:8642 with the FastAPI app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

PORT="${WHISPERX_PORT:-8642}"
HOST="${WHISPERX_HOST:-0.0.0.0}"

echo "Starting WhisperX transcription server on ${HOST}:${PORT}..."
exec uv run uvicorn transcription_server.server:app \
    --host "$HOST" \
    --port "$PORT"
```

**Step 2: Make executable**

Run: `chmod +x transcription_server/run.sh`

**Step 3: Test launch script**

Run: `./transcription_server/run.sh` and verify it starts. Ctrl+C to stop.

**Step 4: Commit**

```bash
git add transcription_server/run.sh
git commit -m "feat: add launch script for transcription server"
```

---

### Summary

After all tasks:

- `uv sync --extra server` installs deps
- `./transcription_server/run.sh` starts the server on port 8642
- Pi calls `curl -X POST "http://<ip>:8642/transcribe?language=fr" -F "file=@recording.wav"`
- Server returns `{"text": "...", "language": "fr"}`
- `/health` endpoint for monitoring
- Optional `?diarize=true` for speaker identification
- GPU memory managed with asyncio.Lock + gc/cache cleanup
