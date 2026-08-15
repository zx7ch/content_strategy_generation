# Content Research Release Audit — 2026-08-13

## Scope and evidence

- Built the macOS PyInstaller Runtime from the current workspace.
- Exercised the frozen Runtime under an isolated temporary HOME.
- Verified Cookie save with a synthetic non-secret Cookie and QR creation without recording the QR payload.
- Ran the targeted login/backend tests; attempted the Creator Playwright suite.

## Fixed before this audit closed

### P0 — Packaged Runtime could not save Cookie or display QR login

**Steps:** Use the release zip, enter a Cookie and save, or press QR login.

**Prior behavior:** Cookie save returned HTTP 500 because frozen Runtime omitted the lazy `curl_cffi` import. QR initialization omitted lazy `qrcode`, which produced `qr_render_failed` and no image.

**Impact:** A fresh user had no functional Xiaohongshu login path, so Content Research could not progress to source collection.

**Fix:** Explicitly declare `curl_cffi` and `qrcode` as PyInstaller hidden imports, protected by `test_runtime_bundle_declares_lazy_xhs_login_dependencies`.

**Verification:** New frozen Runtime returned Cookie save HTTP 200 with a redacted login status. QR start returned HTTP 200 with `status=pending`, a QR data URL, and no failure code. The new archive passed `unzip -t`.

## Open findings

### P1 — Presearch hides the actual failure and incorrectly blames login

**Steps:** Start Content Research with any failing presearch dependency, including LLM connection, Runtime API failure, or unavailable source service.

**Current behavior:** The catch-all UI message is `内容调研预检索失败，请检查 runtime 或小红书登录态。`

**Impact:** Users are led toward Xiaohongshu login even when the true cause is the LLM key, model, proxy, API contract, or server error. This is exactly the ambiguity that extended the earlier LLM investigation.

**Evidence:** `frontend/src/app/creator/page.tsx` catches all presearch errors without retaining `ContentResearchApiError` details.

### P1 — Xiaohongshu Login card discards API error details

**Steps:** Save a malformed Cookie, encounter a SQLite/Runtime issue, or have QR startup fail.

**Current behavior:** Cookie failure always reads `Cookie 保存失败，请确认后重试`; QR startup always reads `二维码暂不可用，请稍后重试或粘贴 Cookie`.

**Impact:** The UI hides the backend's safe codes such as `INVALID_XHS_COOKIE`, `XHS_LOGIN_UNAVAILABLE`, or the QR failure code. A user cannot distinguish a malformed Cookie from a Runtime outage or a service-side QR failure.

**Evidence:** `frontend/src/components/content-research/XiaohongshuLoginCard.tsx` catches without inspecting the error response.

### P2 — Login controls permit duplicate in-flight operations

**Steps:** Double-click “扫码登录” while the 45-second QR request is pending, or double-click “保存 Cookie”.

**Current behavior:** The buttons have no busy state and remain enabled. QR session creation is server-side idempotent while pending, but the UI provides no progress state or protection against repeated requests.

**Impact:** Users can perceive the app as unresponsive, make redundant requests, and receive inconsistent transient feedback while QR polling is active.

**Evidence:** `XiaohongshuLoginCard` tracks `qrPending` but does not bind it to button disabled/loading UI; Cookie save has no pending state.

### P2 — Creator browser release suite has no effective completion boundary

**Steps:** Run `.venv/bin/pytest -q tests/e2e/test_content_research_creator_browser.py`.

**Current behavior:** The suite remained running for more than five minutes at negligible CPU and did not emit a pass, fail, or timeout; it had to be stopped for the release audit to continue.

**Impact:** CI/release confidence is weakened: a blocked browser test can hold release verification indefinitely and obscures the test/action that is waiting.

**Coverage note:** This audit therefore does not claim the entire browser suite passed. Targeted backend login tests passed, and frozen-artifact endpoint checks passed.

## Passed checks

- `tests/unit/test_runtime_launcher.py`, `tests/unit/test_xhs_credentials.py`, `tests/unit/test_xhs_qr_auth.py`, and `tests/e2e/test_xhs_login_api.py`: 14 passed.
- Frozen Cookie save: HTTP 200, redacted `manual_cookie` status.
- Frozen QR start: HTTP 200, pending state, QR image data URL, no failure code.
- Release archive integrity: passed.
