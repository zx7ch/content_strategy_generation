# Runtime Credential Setup Design

## Goal

Configure the LLM and Xiaohongshu authentication entirely from Creator's right
sidebar. Users must not edit a packaged `config.env`, and a successful login
must survive a Runtime restart and application upgrade.

## Scope

- Keep the existing Model Service card as the LLM configuration surface.
- Add a Xiaohongshu Login card to the same right sidebar.
- Offer QR login and Cookie paste side by side, without failure-dependent
  visibility rules.
- Persist the selected Xiaohongshu credentials locally and restore them on
  Runtime startup.
- Correct Runtime path resolution so all user-owned state is outside the
  executable bundle.

## Explicit non-goals

- No onboarding page, modal, or first-run wizard.
- No cloud proxy, remote credential storage, or server-side credential readback.
- No platform-specific Keychain integration in this release.

## User experience

The right sidebar contains two independent cards:

1. **Model Service** retains the existing Base URL, model, API Key, test, save,
   and recovery controls.
2. **Xiaohongshu Login** shows both methods immediately:
   - `扫码登录`: starts a local QR attempt and renders its QR image and status.
   - `粘贴 Cookie`: accepts a Cookie in a password field and exposes Save,
     Replace, and Clear actions.

After either method succeeds, the card shows only the safe projection:
`已登录`, login source (`扫码` or `Cookie`), and last-updated time. It never
returns or renders the Cookie value. A failed upstream authentication changes
the status to `需要重新登录`; both login methods remain available.

The user documentation points to the right sidebar. `config.env` is not a
normal setup step.

## Local persistence and startup recovery

Add a local credential store in the Runtime's user-owned SQLite database. It
holds one active Xiaohongshu credential record per local Runtime:

| Field | Purpose |
| --- | --- |
| `credential_id` | Internal record identifier |
| `credential_value` | Cookie secret; never part of a public API response, Trace, log, or report |
| `source` | `qr` or `manual_cookie` |
| `updated_at` | Safe status projection and replacement audit |
| `status` | `active` or `cleared` |

On QR completion, serialize the authenticated upstream auth into its Cookie
header and atomically replace the active record. On manual save, validate that
the submitted Cookie is non-empty, atomically replace the active record, and
construct the same upstream auth representation. On Runtime startup, load the
active record before creating the Spider client. The Spider client's auth
provider therefore starts authenticated after restart without another QR scan.

LLM configuration retains its existing local SQLite storage and safe masked
read model. Neither LLM keys nor Xiaohongshu cookies are stored in the release
ZIP or returned to the frontend after save.

## Runtime package boundary

`runtime_main.py` owns the user-data paths. It must force database, Chroma,
thread-store, discovery-store, and HuggingFace cache paths into the stable user
data directory before the application settings are imported. The bundled
`config.env` may provide non-sensitive defaults only; it must not override any
user-data path or contain LLM credentials or Spider cookies.

This makes replacement of the extracted Runtime folder safe: existing SQLite
records, including LLM configuration and Xiaohongshu credentials, remain in
the user-data directory and are reopened by the next Runtime version.

## API boundary

Add local-only endpoints for the login card:

- Read a redacted login status.
- Start and poll an existing QR attempt.
- Save/replace a manually pasted Cookie.
- Clear the saved credential.

Every response returns only status, source, timestamps, and safe failure codes.
The request body carrying a Cookie is accepted only by the local Runtime and is
excluded from application logs, Trace payloads, persistence projections, and
error strings.

## Failure handling

- QR rendering/login failure leaves any last known active credential untouched
  until a new login succeeds.
- Cookie validation failure never replaces a working saved credential.
- An upstream authentication failure marks the public status stale and directs
  the user to either existing card action; it does not expose upstream payloads.
- Clear deletes the active credential and leaves the Runtime unauthenticated.

## Verification

- Unit tests prove redacted status never contains a Cookie and that failed
  replacement preserves the existing credential.
- Runtime restart tests save QR-equivalent and manual Cookie credentials,
  reconstruct the Runtime, and verify the Spider auth provider is ready.
- API tests cover QR status, manual save/replace/clear, and no-secret error
  projections.
- Creator tests cover simultaneous visibility of QR and Cookie controls plus
  masked persisted status.
- Packaging tests run the built Runtime with a clean temporary HOME, verify no
  user database is created inside the bundle, then restart it and verify both
  LLM configuration and Xiaohongshu login status persist.
