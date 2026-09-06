# Avoria

Avoria is a local-first media processing application built with FastAPI,
Celery, RabbitMQ, FFmpeg, and React. Media files are stored locally, while
processing jobs run asynchronously in a separate worker.

## Features

- Media upload and FFprobe inspection
- Video and audio conversion
- Video compression
- Audio extraction and replacement
- Mute and volume adjustment
- Frame-accurate trim
- Playback speed adjustment
- Preset crop and fit
- Ordered multi-video merge with normalization
- Turkish and English interface with light and dark themes
- Home-page quick start and step-by-step video/audio tool guides

## Requirements

- Python 3.11 or newer
- Node.js 22 or newer
- RabbitMQ
- FFmpeg and FFprobe available on `PATH`

The commands below target PowerShell on Windows.

## Configuration

Create the local environment file from the provided example:

```powershell
Copy-Item .env.example .env
```

The defaults use:

- API prefix: `/api/v1`
- RabbitMQ: `localhost:5672`
- Uploads: `data/uploads`
- Outputs: `data/outputs`
- Maximum upload size: 2 GB per file
- FFmpeg timeout: 6 hours per FFmpeg process

All settings can be changed through `.env` variables prefixed with `AVORIA_`.

## Run the application

### 1. Start the API

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
uvicorn backend.app.main:app --reload
```

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`

### 2. Start the worker

Start RabbitMQ, then open a second terminal in the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Resolve-Path backend).Path
celery -A app.core.celery_app:celery_app worker --loglevel=info --pool=solo
```

`--pool=solo` is required for the Windows development worker.

### 3. Start the frontend

```powershell
Set-Location frontend
npm install
npm run dev
```

The frontend is available at `http://localhost:5173`.

## API workflow

1. Upload each source file with `POST /api/v1/media/upload`.
2. Optionally inspect it with `POST /api/v1/media/{media_id}/inspect`.
3. Create a processing job with `POST /api/v1/jobs`.
4. Poll `GET /api/v1/jobs/{job_id}` until it completes or fails.
5. Find completed files under `data/outputs/<job_id>.<format>`.

There is currently no output download endpoint. Job history is held by Celery's
result backend and expires after the configured result lifetime.

Supported upload extensions are MP4, MOV, MKV, WebM, AVI, MP3, WAV, M4A, AAC,
FLAC, OGG, and Opus.

Job states are `queued`, `processing`, `completed`, and `failed`. A completed
status includes the output ID and format; failures expose a safe public error
message.

## Processing operations

All operations are submitted to `POST /api/v1/jobs`.

| Operation | Parameters | Notes |
| --- | --- | --- |
| `convert` | `container`, `video_codec`, `audio_codec` | Defaults to MP4, H.264, and AAC. Use `GET /api/v1/media/conversion-options` for locally available combinations. |
| `compress` | `compression_level` | Accepts `light`, `balanced`, or `strong`. |
| `extract_audio` | Top-level `format` | Accepts `mp3`, `wav`, `flac`, `m4a`, `opus`, or `ogg`; defaults to MP3. |
| `mute` | None | Removes audio while preserving the supported video container. |
| `volume` | `volume_percent` | Accepts values from 0 through 200. |
| `trim` | `start_seconds`, `end_seconds` | Performs a frame-accurate range cut. |
| `speed` | `speed` | Accepts factors from 0.25 through 4.0. |
| `crop` | `aspect_ratio`, `mode`, optional background fields | Supports preset crop or fit output. |
| `merge_videos` | `media_ids`, `target_aspect_ratio` | Merges at least two videos in request order. |
| `replace_audio` | `audio_media_id`, optional `loop` | Replaces the target video's audio with an uploaded audio-only file. |

`transcode` remains a backward-compatible alias for `convert`; new clients
should use `convert`.

Except for `merge_videos`, video operations use a root-level `media_id`:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "compress",
  "parameters": {
    "compression_level": "balanced"
  }
}
```

### Crop and fit presets

| Aspect ratio | Output size |
| --- | --- |
| `16:9` | 1920x1080 |
| `9:16` | 1080x1920 |
| `1:1` | 1080x1080 |
| `4:5` | 1080x1350 |

Crop mode removes overflow around a centered frame. Fit mode preserves the
whole frame and supports a solid-color or blurred background.

### Merge videos

Upload every source first, then submit their media IDs in the required order:

```json
{
  "operation": "merge_videos",
  "parameters": {
    "media_ids": [
      "550e8400-e29b-41d4-a716-446655440000",
      "7a6d48cb-56f7-4d43-a06c-e84fdf1f9ad1"
    ],
    "target_aspect_ratio": "9:16"
  }
}
```

`target_aspect_ratio` accepts `16:9`, `9:16`, `1:1`, `4:5`, or
`first_video`. `first_video` maps the first video's ratio to the nearest preset.

Merge normalization:

- Fits each video inside the selected canvas without cropping or stretching
- Uses black padding where needed
- Produces CFR 30 FPS and `yuv420p` video
- Normalizes audio to 48 kHz stereo when any input has audio
- Adds a duration-matched silent track to inputs without audio
- Keeps video-only output when every input is silent
- Uses the first video's supported container profile for the output

Inputs that already match the selected profile can use a direct stream-copy
fast path. Temporary segments are removed after success or failure, and final
outputs are written atomically.

## Supported conversion containers

| Container | Video codecs | Audio codecs |
| --- | --- | --- |
| MP4 | H.264, H.265, AV1, copy, none | AAC, MP3, copy, none |
| MOV | H.264, H.265, copy, none | AAC, PCM, copy, none |
| MKV | H.264, H.265, VP9, AV1, copy, none | AAC, MP3, Opus, Vorbis, FLAC, PCM, copy, none |
| WebM | VP9, AV1, copy, none | Opus, Vorbis, copy, none |
| AVI | H.264, copy, none | MP3, PCM, copy, none |

Audio-only conversion also supports MP3, WAV, FLAC, OGG, M4A, and Opus. The
conversion-options endpoint omits encoders or muxers unavailable in the local
FFmpeg installation.

## Project structure

```text
backend/app/api             HTTP routes
backend/app/application     Application ports and job contracts
backend/app/core            Configuration, logging, and Celery setup
backend/app/infrastructure  Queue and local storage adapters
backend/app/processing      FFmpeg and FFprobe processing services
backend/app/workers         Celery task orchestration
backend/tests               Backend tests
frontend                    React/Vite client
data                        Local uploads, outputs, and logs
```

## Tests

Run the backend suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```

Tests do not require RabbitMQ. FFmpeg integration tests run when FFmpeg and
FFprobe are installed; otherwise they are skipped.
