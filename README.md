# Avoria

Avoria is a local-first media processing platform foundation built as a modular monolith with a separate RQ worker process.

This repository currently contains infrastructure only. Media upload, conversion, compression, analysis, progress reporting and processing jobs are intentionally not implemented yet.

## Architecture

- `frontend`: React client built with Vite.
- `backend/app/api`: HTTP transport and route composition.
- `backend/app/application`: use-case and application-service boundary.
- `backend/app/core`: configuration, logging and shared application errors.
- `backend/app/database`: SQLite engine, session and declarative model base. SQLite is reserved for authentication data.
- `backend/app/infrastructure`: Redis/RQ and local filesystem adapters.
- `backend/app/processing`: FFmpeg/FFprobe configuration boundary. Runners, parsers and operation handlers will live here later.
- `backend/app/realtime`: WebSocket connection lifecycle. Worker events will later reach this layer through Redis, never process-local worker memory.
- `backend/app/workers`: independent RQ worker process entry point.

Redis holds queue data and, in later phases, temporary job state and progress. Media files and technical log files live under `data/`. Processing history is not persisted to SQLite.

## Requirements

- Python 3.11 or newer
- Node.js 22 or newer
- Redis available at the configured URL
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

The API is available at `http://127.0.0.1:8000`; the health endpoint is `GET /api/v1/health`.

## Redis and RQ worker

Start a local Redis server before starting the worker. Redis installation is intentionally left platform-specific; set `AVORIA_REDIS_URL` if it does not run at `redis://localhost:6379/0`.

In a second activated terminal, from the repository root:

```powershell
python -m backend.app.workers.main
```

On Windows the entry point uses RQ `SpawnWorker`; on other platforms it uses the standard RQ worker. The queue uses JSON serialization. No jobs are registered or enqueued in this phase.

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

The tests do not require a running Redis server.

## Planned extension points

- FFmpeg runner, FFprobe adapter/parser and progress parser: `backend/app/processing`
- Processing use-cases and operation handlers: `backend/app/application` and `backend/app/processing`
- Runtime job state and progress transport: Redis adapters in `backend/app/infrastructure`
- Worker-to-client progress: Redis to `backend/app/realtime` WebSockets
- Privacy and HLS: new processing operations, not separate services

