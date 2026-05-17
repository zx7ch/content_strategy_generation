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
