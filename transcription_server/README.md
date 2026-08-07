# WhisperX Transcription Server

Local FastAPI server wrapping [whisperx](https://github.com/m-bain/whisperX) for audio transcription.
Designed to run on a GPU workstation and accept audio uploads from a Raspberry Pi (or any HTTP client) on the local network.

## Docker (recommended)

```bash
cd transcription_server
docker compose up -d
```

Manage the container:

```bash
docker compose logs -f          # follow logs
docker compose restart           # restart
docker compose down              # stop
docker compose up -d --build     # rebuild after code changes
```

The container auto-restarts on reboot (`unless-stopped` policy).

## Bare metal (alternative)

```bash
cd transcription_server
uv sync                          # first time only
./run.sh
```

## Usage

The model loads at startup (~30-90 seconds). Server is ready when `/health` returns `model_loaded: true`.

### Transcribe audio

```bash
# Auto-detect language
curl -X POST "http://<server-ip>:8642/transcribe" \
  -F "file=@recording.wav"

# Specify language (recommended for short clips)
curl -X POST "http://<server-ip>:8642/transcribe?language=fr" \
  -F "file=@recording.wav"

curl -X POST "http://<server-ip>:8642/transcribe?language=en" \
  -F "file=@recording.wav"
```

Response:

```json
{"text": "The full transcript as plain text.", "language": "fr"}
```

### Speaker diarization

Requires a HuggingFace token with access to [pyannote models](https://huggingface.co/pyannote/speaker-diarization-3.1):

```bash
export HF_TOKEN="hf_your_token_here"
./transcription_server/run.sh
```

Then:

```bash
curl -X POST "http://<server-ip>:8642/transcribe?diarize=true&min_speakers=2&max_speakers=2&language=fr" \
  -F "file=@recording.wav"
```

### Health check

```bash
curl http://<server-ip>:8642/health
# {"status": "ok", "model_loaded": true}
```

### Interactive API docs

Visit `http://<server-ip>:8642/docs` in a browser for the auto-generated Swagger UI.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WHISPERX_DEVICE` | `cuda` | Device (`cuda` or `cpu`) |
| `WHISPERX_MODEL` | `large-v2` | Whisper model size |
| `WHISPERX_COMPUTE_TYPE` | `float32` | Compute type (`float16`, `float32`, `int8`) |
| `WHISPERX_BATCH_SIZE` | `16` | Batch size (reduce if low on VRAM) |
| `WHISPERX_HOST` | `0.0.0.0` | Bind address |
| `WHISPERX_PORT` | `8642` | Port |
| `HF_TOKEN` | *(empty)* | HuggingFace token (required for diarization) |

## Supported Formats

Any format supported by ffmpeg: wav, mp3, m4a, flac, ogg, oga, webm, etc.

## Notes

- Auto-detect can misidentify language on short clips. Specifying `language` explicitly is more reliable.
- The model stays in GPU VRAM while the server runs (~6-8 GB for large-v2 with float32).
- Only one transcription runs at a time (GPU access is serialized).
