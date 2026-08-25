# Avoria

Avoria is a local-first media processing platform foundation built as a modular monolith with a separate Celery worker process.

The backend supports media upload, FFprobe inspection, user-selectable media conversion, video compression, multi-format audio extraction, stream-copy video muting, volume adjustment, frame-accurate trim/cut, playback speed control, and external audio replacement through RabbitMQ, Celery, and FFmpeg. Processing job history is not persisted to SQLite.

## Architecture

- `frontend`: React client built with Vite.
- `backend/app/api`: HTTP transport and route composition.
- `backend/app/application`: use-case and application-service boundary.
- `backend/app/core`: configuration, logging and shared application errors.
- `backend/app/database`: SQLite engine, session and declarative model base. SQLite is reserved for authentication data.
- `backend/app/infrastructure`: Celery job-state and local filesystem adapters.
- `backend/app/processing`: FFprobe inspection, conversion compatibility, command building, local FFmpeg capability detection, and process execution.
- `backend/app/realtime`: WebSocket connection lifecycle.
- `backend/app/workers`: Celery media task orchestration.

RabbitMQ transports tasks and Celery's transient RPC result backend exposes task state. Uploads, processing outputs, and technical log files live under `data/`.

## Requirements

- Python 3.11 or newer
- Node.js 22 or newer
- RabbitMQ 4.2.x running as a Windows service on `localhost:5672`
- FFmpeg and FFprobe installed or configured with explicit executable paths

Docker is not used.

## Configuration

Copy `.env.example` to `.env` and adjust the values for your machine:

```powershell
Copy-Item .env.example .env
```

All paths in the example are relative to the repository root. The application creates the configured storage, log and SQLite parent directories when it starts.

## Backend setup

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
uvicorn backend.app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`; Swagger is available at `http://127.0.0.1:8000/docs`.

## RabbitMQ and Celery worker

Start the RabbitMQ Windows service before creating jobs. The development defaults are guest/guest on `localhost:5672`, vhost `/`; the optional management UI is `http://localhost:15672`. RabbitMQ installation and management API integration are outside this repository.

In a second activated terminal, from the repository root:

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
celery -A app.core.celery_app:celery_app worker --loglevel=info --pool=solo
```

The `PYTHONPATH` line makes the requested short `app.core` module path available while commands are run from the repository root. The `--pool=solo` option is required for the Windows development worker. The queue and result payloads use JSON serialization, and the application job UUID is used as the Celery task ID.

One worker processes one CPU-intensive media job at a time. Start additional workers only deliberately: too many concurrent FFmpeg processes can exhaust CPU, RAM, and disk I/O.

## Processing jobs

Upload media through `POST /api/v1/media/upload`. The default upload allowlist is `mp4`, `mov`, `mkv`, `webm`, `avi`, `mp3`, `wav`, `m4a`, `aac`, `flac`, `ogg`, and `opus`. The upload response contains the `media_id` required by inspection and processing requests.

Inspect the uploaded file with:

```text
POST /api/v1/media/{media_id}/inspect
```

This uses FFprobe and returns format, duration, video stream, and audio stream metadata. The frontend can discover the conversion choices supported by the configured local FFmpeg build with:

```text
GET /api/v1/media/conversion-options
```

The default conversion is MP4 with H.264 video and AAC audio. Create it in Swagger with:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "convert"
}
```

Or select an explicit conversion:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "convert",
  "parameters": {
    "container": "webm",
    "video_codec": "vp9",
    "audio_codec": "opus"
  }
}
```

`transcode` remains accepted as a temporary backward-compatible alias and uses the same typed conversion parameters and pipeline. New clients should use `convert`.

Create a video compression job through the same endpoint and queue:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "compress",
  "parameters": {
    "compression_level": "balanced"
  }
}
```

The supported API levels are `light`, `balanced` (the default), and `strong`. Compression preserves supported source containers: MP4 and MOV use H.264/AAC, MKV uses H.264/AAC in Matroska, WebM uses VP9/Opus, and AVI uses MPEG-4 Part 2/MP3. Encoder-specific quality values remain internal. Compression does not add resolution or frame-rate filters. Completed jobs include original/output sizes, saved bytes, reduction percentage, and whether the output is smaller. An output larger than its input is still a successful result.

Extract the first audio stream as MP3 (the default), WAV, FLAC, M4A, Opus, or OGG:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "extract_audio",
  "format": "ogg"
}
```

Omitting `format` preserves the MP3 default. MP3 uses `libmp3lame` at
application-controlled quality 2; WAV uses 16-bit PCM (`pcm_s16le`); FLAC uses
the lossless `flac` encoder without user-selectable tuning; M4A uses AAC at a
fixed 192 kbps; Opus uses `libopus` at a fixed 128 kbps in an Ogg container;
OGG uses `libvorbis` at application-controlled quality 5. Opus remains a
separate `.opus` output. Raw AAC/ADTS output is not supported. The worker uses
FFprobe to reject inputs without an audio stream before starting FFmpeg.
Completed outputs are finalized atomically under `data/outputs/` with the
selected `.mp3`, `.wav`, `.flac`, `.m4a`, `.opus`, or `.ogg` extension.

Remove all audio from a video while preserving its source container and copying
the primary video stream without re-encoding:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "mute"
}
```

Mute accepts no processing parameters. It preserves MP4, MOV, MKV, WebM, and
AVI containers, and deliberately omits audio, subtitle, and data streams.

Adjust all audio streams in a video from 0% through 200%:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "volume",
  "parameters": {
    "volume_percent": 50
  }
}
```

Volume preserves MP4, MOV, MKV, WebM, and AVI containers and stream-copies the
primary video. Audio is re-encoded because FFmpeg's volume filter is applied:
MP4, MOV, and MKV use AAC, WebM uses Opus, and AVI uses MP3. Silent videos are
rejected because they do not contain an audio stream to adjust.

Trim a decimal timestamp range while preserving the supported source container:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "trim",
  "parameters": {
    "start_seconds": 10.25,
    "end_seconds": 25.75
  }
}
```

`trim` accepts finite integer or decimal seconds and targets frame-accurate cuts
by seeking after input decoding and re-encoding rather than using keyframe-only
stream copy. Video+audio, video-only, and audio-only inputs are supported; all
audio streams are retained. Video containers use the existing MP4, MOV, MKV,
WebM, and AVI compression profiles. Audio-only inputs preserve the existing MP3,
WAV, FLAC, M4A, Opus, or OGG profile and extension. Raw AAC/ADTS is not a
source-preserving trim target. Subtitle and data streams are omitted.

Change playback speed independently on an original upload or any other media ID:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "speed",
  "parameters": {
    "speed": 1.5
  }
}
```

`speed` accepts finite numeric factors from `0.25` through `4.0`, including
`1.0` (which follows the same deterministic processing path). It supports
video+audio, video-only, and audio-only media, preserves every audio stream,
and retains the supported source container. Video timestamps are changed with
`setpts`; audio uses a pitch-preserving, safely chained `atempo` filter. Filtered
streams are re-encoded with the existing centralized video/audio profiles.
Subtitle and data streams are omitted. Speed is a standalone operation: trim,
mute, volume, compression, and processing pipelines are not prerequisites.

Replace every existing target-video audio stream with one uploaded audio file:

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "replace_audio",
  "parameters": {
    "audio_media_id": "7a6d48cb-56f7-4d43-a06c-e84fdf1f9ad1"
  }
}
```

`audio_media_id` must identify a separate, supported audio-only upload; video
files are not accepted as the external source. The target may contain audio or
be video-only, but an audio-only target is rejected. The primary target video
is stream-copied without re-encoding, all of its old audio streams are omitted,
and the first external audio stream becomes the output's only audio stream. The
target video selects the MP4, MOV, MKV, WebM, or AVI output container and the
existing centralized profile selects its compatible audio encoder.

The target video is always the duration master. Longer external audio is cut at
the video duration; shorter external audio plays once and is deterministically
padded with silence through the end of the video. Audio looping is deliberately
not supported in this phase. `replace_audio` is standalone and needs only the
original video and audio uploads; mute, extraction, trim, speed, and other
operations are not prerequisites.

| Extraction format | Extension | Encoder | Muxer | Application policy |
| --- | --- | --- | --- | --- |
| MP3 | `.mp3` | `libmp3lame` | `mp3` | Quality 2 |
| WAV | `.wav` | `pcm_s16le` | `wav` | 16-bit PCM |
| FLAC | `.flac` | `flac` | `flac` | Lossless |
| M4A | `.m4a` | `aac` | `ipod` | 192 kbps |
| Opus | `.opus` | `libopus` | `ogg` | 128 kbps |
| OGG | `.ogg` | `libvorbis` | `ogg` | Quality 5 |

| Compression container | Video encoder | Audio encoder | Quality |
| --- | --- | --- | --- |
| MP4 | libx264 | aac | CRF 20 / 23 / 28 |
| MOV | libx264 | aac | CRF 20 / 23 / 28 |
| MKV | libx264 | aac | CRF 20 / 23 / 28 |
| WebM | libvpx-vp9 | libopus | CRF 26 / 32 / 38 with `-b:v 0` |
| AVI | mpeg4 | libmp3lame | `-q:v` 3 / 5 / 8 |

The source suffix and normalized FFprobe format name must agree. Unsupported containers fail instead of silently falling back to MP4. Outputs are finalized atomically as `data/outputs/<job_id>.<source-extension>` from `<job_id>.part.<source-extension>`.

### Conversion compatibility

| Container | Video codecs | Audio codecs |
| --- | --- | --- |
| MP4 | h264, h265, av1, copy, none | aac, mp3, copy, none |
| MKV | h264, h265, vp9, av1, copy, none | aac, mp3, opus, vorbis, flac, pcm, copy, none |
| WebM | vp9, av1, copy, none | opus, vorbis, copy, none |
| MOV | h264, h265, copy, none | aac, pcm, copy, none |
| AVI | h264, copy, none | mp3, pcm, copy, none |
| MP3 | none | mp3, copy |
| WAV | none | pcm, copy |
| FLAC | none | flac, copy |
| OGG | none | opus, vorbis, flac, copy |
| M4A | none | aac, copy |
| Opus | none | opus, copy |

API codec IDs are stable application values, not raw FFmpeg arguments. They are mapped internally to vetted encoders such as `h264 -> libx264`, `vp9 -> libvpx-vp9`, and `opus -> libopus`. AV1 uses `libaom-av1`; the capabilities endpoint omits encoders and muxers unavailable in the configured FFmpeg build, and the worker validates availability again before execution.

`copy` performs stream copy when the input codec is compatible with the selected container. `none` omits that stream; disabling both streams is rejected. A missing audio stream is tolerated for video output. Audio-only input requires `video_codec: none`. Audio containers are supported by Convert as representation conversion; Extract Audio remains a separate, policy-controlled workflow and currently selects only the first audio stream.

Only the primary video and primary audio streams are selected. Subtitle and data streams are deliberately omitted in this version.

Create and query jobs with:

```text
POST /api/v1/jobs
GET  /api/v1/jobs/{job_id}
```

Client-facing states are `queued`, `processing`, `completed`, and `failed`, mapped from Celery task states. The status response includes progress when the worker has reported it, output format on completion, and a safe error message on failure. Outputs are written atomically as `data/outputs/<job_id>.<container>` using partial files such as `<job_id>.part.webm`.

The conversion development flow is:

```text
Upload -> Inspect -> Conversion options -> Create job -> Poll job status -> Check output
```

Audio extraction, compression, mute, volume adjustment, trim, speed control, and external audio replacement use the same processing queue without the conversion-options step:

```text
Upload -> Inspect -> Create processing job -> Poll job status -> Check output
```

RabbitMQ must be running and a Celery worker must be ready before creating any processing job. Upload, inspection, and conversion-options requests do not require a worker.

## Frontend setup

In another terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

The frontend is available at `http://localhost:5173`.

## Tests

From the repository root with the virtual environment active:

```powershell
pytest backend\tests
```

The automated tests use fakes and do not require a running RabbitMQ service or FFmpeg executable.
