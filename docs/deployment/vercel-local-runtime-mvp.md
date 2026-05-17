# Vercel Frontend + Local Runtime MVP

This document describes the first deployable path:

- Frontend: deployed to Vercel.
- Backend: installed and started on the user's local machine.
- Updates: users download the new local runtime installer and install over the old version.

## Frontend Deployment

Create a Vercel project from the repository and set:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | `Next.js` |
| Install Command | `npm install` |
| Build Command | `npm run build` |
| Output Directory | Vercel default |

Set production environment variables:

```bash
NEXT_PUBLIC_XHS_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_XHS_AUTH_TOKEN=
```

The frontend must call the local runtime from browser-side code. Vercel server code cannot call `localhost:8000`, because that would point to the Vercel environment rather than the user's machine.

## Local Runtime Configuration

After the Vercel production domain is available, add it to the local runtime `.env`:

```bash
XHS_RUNTIME_SERVICE_NAME=xhs-agent-runtime
XHS_RUNTIME_VERSION=0.1.0
XHS_RUNTIME_API_CONTRACT=local-runtime-v1
XHS_CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://your-app.vercel.app
```

Start the runtime on loopback:

```bash
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Check the minimum runtime contract:

```bash
curl http://127.0.0.1:8000/health
```

Expected shape:

```json
{
  "service": "xhs-agent-runtime",
  "status": "healthy",
  "version": "0.1.0",
  "api_contract": "local-runtime-v1"
}
```

The response may include extra fields such as `timestamp` and `queue` for current API compatibility.

## User Update Flow

For the MVP, backend updates are manual:

1. Publish a new local runtime installer or packaged archive.
2. User downloads the new version.
3. User installs over the old version.
4. Existing local data under the configured SQLite and Chroma paths is preserved.
5. The updated runtime reports its new `XHS_RUNTIME_VERSION` through `/health`.

Before shipping an installer update, include:

- Runtime version.
- API contract version.
- Release notes.
- Any required migration notes.
- Whether the Vercel frontend requires this runtime version.

Automatic updates, Electron/Tauri shells, pairing tokens, and full installer pipelines are later phases.

---

## Building the Local Runtime Executable

The local runtime is packaged with PyInstaller into a self-contained folder (`dist/xhs-runtime/`).

### Prerequisites

- Python 3.10–3.12 with all project dependencies installed (`pip install -r requirements.txt`)
- `pyinstaller` (installed automatically by the build script if missing)
- Internet access during the build step to pre-download the embedding model

### Build

```bash
./scripts/build_runtime.sh
# or with a clean rebuild:
./scripts/build_runtime.sh --clean
```

The script:
1. Pre-downloads `BAAI/bge-base-zh-v1.5` (~400 MB) into the HuggingFace cache so it is bundled into the exe.
2. Runs `pyinstaller runtime_main.spec`.
3. Outputs `dist/xhs-runtime/` (~600–800 MB).

### Distributing

Zip and distribute the entire `dist/xhs-runtime/` folder. Users run:

```bash
# macOS / Linux
./xhs-runtime/xhs-runtime

# Windows
xhs-runtime\xhs-runtime.exe
```

On first run the runtime creates `data/xhs_agent.db`, `data/xhs_agent_discovery.db`, and `data/chroma/` next to the executable.

Users configure their API keys by placing a `.env` file next to the executable:

```bash
ANTHROPIC_API_KEY=sk-ant-...
XHS_CORS_ALLOWED_ORIGINS=https://your-app.vercel.app
```

### Key files

| File | Purpose |
|---|---|
| `runtime_main.py` | Executable entry point — starts uvicorn on `127.0.0.1:8000` |
| `runtime_main.spec` | PyInstaller build spec |
| `scripts/build_runtime.sh` | Build helper script |

### Notes

- The output is a **folder** (`dist/xhs-runtime/`), not a single file. PyInstaller's `--onefile` mode is avoided because ChromaDB's native extensions do not extract reliably under some OS security policies.
- Embedding model inference requires a CPU with AVX2 support (standard on any machine from ~2013 onward).
- Data files (`data/`) are never inside the exe folder — they live next to it and persist across runtime upgrades.
