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
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp_path = os.path.join(UPLOAD_DIR, f"{os.urandom(8).hex()}{suffix}")
    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

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
