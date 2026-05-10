from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from experiments.xhs_extension_mvp.server.logging_utils import configure_logging, get_logger
from experiments.xhs_extension_mvp.server.models import (
    ActiveSearchContextRequest,
    ActiveTaskResponse,
    AutoScrapeRequest,
    AutoScrapeResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    CustomQueryRequest,
    CustomQueryResponse,
    DeleteCustomQueryResponse,
    ExtensionCaptureRequest,
    ExtensionCaptureResponse,
    ExtensionHealthResponse,
    HotspotSnapshotResponse,
    ManualSeedRequest,
    ManualSeedResponse,
    ScraperReadinessResponse,
    ScrapeStatusResponse,
    TaskSnapshotResponse,
    TaskSnapshotVersionResponse,
)
from experiments.xhs_extension_mvp.server.scraper import scrape_search_feed
from experiments.xhs_extension_mvp.server.scraper_login import is_logged_in
from experiments.xhs_extension_mvp.server.scraper_models import ScrapePhase, ScrapeProgress
from experiments.xhs_extension_mvp.server.scraper_runtime import ScraperRuntime
from experiments.xhs_extension_mvp.server.scraper_state import ScrapeStateRegistry
from experiments.xhs_extension_mvp.server.storage import (
    InvalidCaptureToken,
    MVPStorage,
    default_mvp_db_path,
    default_secret,
    utc_now,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_PROFILE_DIR_DEFAULT = Path("data/chrome-profile")


class _ReadinessCache:
    """Short-lived in-process cache for scraper readiness probes (single-worker)."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._value: Optional[ScraperReadinessResponse] = None

    def get_fresh(self) -> Optional[ScraperReadinessResponse]:
        if self._value is None:
            return None
        age = (utc_now() - self._value.last_checked_at).total_seconds()
        return self._value if age <= self._ttl else None

    def put(self, value: ScraperReadinessResponse) -> None:
        self._value = value


async def _run_scrape_background(
    *,
    runtime: ScraperRuntime,
    registry: ScrapeStateRegistry,
    storage: MVPStorage,
    task_id: str,
    keyword: str,
    scroll_count: int,
) -> None:
    """Drive scrape, push progress into registry, ingest results into SQLite."""
    logger = get_logger()

    async def _on_progress(progress: ScrapeProgress) -> None:
        await registry.update(task_id, progress)

    try:
        items = await scrape_search_feed(
            keyword,
            runtime=runtime,
            scroll_count=scroll_count,
            on_progress=_on_progress,
        )
        if items:
            captured, imported = storage.ingest_scraper_items(
                task_id=task_id,
                keyword=keyword,
                items=items,
            )
            logger.info(
                "Scraper ingested items",
                extra={
                    "event_name": "mvp_scraper_ingested",
                    "task_id": task_id,
                    "captured": captured,
                    "imported": imported,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background scrape failed", extra={"task_id": task_id})
        current = registry.get(task_id)
        error_progress = (
            current.progress if current is not None else ScrapeProgress(scroll_total=scroll_count)
        )
        await registry.update(
            task_id,
            error_progress.with_phase(ScrapePhase.ERROR, error_message=str(exc)),
        )
    finally:
        await registry.release(task_id)


def create_app(
    *,
    database_path: str | Path | None = None,
    secret: str | None = None,
    profile_dir: Path | None = None,
) -> FastAPI:
    logger = configure_logging(force=True)
    storage = MVPStorage(database_path or default_mvp_db_path(), secret=secret or default_secret())
    storage.init_db()
    _profile_dir = Path(profile_dir) if profile_dir is not None else _PROFILE_DIR_DEFAULT

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        scraper_runtime = ScraperRuntime(profile_dir=_profile_dir)
        scrape_registry = ScrapeStateRegistry()
        readiness_cache = _ReadinessCache(ttl_seconds=60)
        application.state.mvp_storage = storage
        application.state.scraper_runtime = scraper_runtime
        application.state.scrape_state_registry = scrape_registry
        application.state.readiness_cache = readiness_cache
        logger.info("Started XHS extension MVP app", extra={"event_name": "mvp_app_started"})
        yield
        await scraper_runtime.shutdown()
        logger.info("Stopped XHS extension MVP app", extra={"event_name": "mvp_app_stopped"})

    app = FastAPI(
        title="XHS Extension MVP",
        version="0.1.0",
        lifespan=lifespan,
    )

    # The MVP web page and Chrome extension both call this local service directly.
    # Allowing cross-origin requests avoids opaque "Failed to fetch" errors from
    # chrome-extension:// origins during capture submission.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def disable_workspace_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers.update(NO_CACHE_HEADERS)
        return response

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="mvp-static")

    @app.get("/", include_in_schema=False)
    async def serve_workspace() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers=NO_CACHE_HEADERS)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/extension/health", response_model=ExtensionHealthResponse)
    async def extension_health() -> ExtensionHealthResponse:
        return ExtensionHealthResponse(
            status="ok",
            server_time=utc_now(),
            active_task_available=storage.has_active_task(),
        )

    @app.get("/api/extension/active-task", response_model=ActiveTaskResponse)
    async def get_active_task() -> ActiveTaskResponse:
        return storage.get_active_task_response()

    @app.post("/api/tasks/{task_id}/activate", response_model=ActiveTaskResponse)
    async def activate_task(task_id: str) -> ActiveTaskResponse:
        try:
            return storage.activate_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.post("/api/tasks/{task_id}/active-search-context", response_model=ActiveTaskResponse)
    async def set_active_search_context(task_id: str, payload: ActiveSearchContextRequest) -> ActiveTaskResponse:
        try:
            return storage.set_active_search_context(
                task_id=task_id,
                query=payload.query,
                source=payload.source,
                opened_at=payload.opened_at,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.get("/api/tasks/{task_id}/snapshot", response_model=TaskSnapshotVersionResponse)
    async def get_task_snapshot_version(task_id: str) -> TaskSnapshotVersionResponse:
        snapshot = storage.get_task_snapshot_version(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return snapshot

    @app.get("/api/tasks/{task_id}/candidate-directions", response_model=TaskSnapshotResponse)
    async def get_candidate_directions(task_id: str) -> TaskSnapshotResponse:
        snapshot = storage.get_task_snapshot(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return snapshot

    @app.post("/mvp/tasks", response_model=CreateTaskResponse)
    async def create_task(payload: CreateTaskRequest) -> CreateTaskResponse:
        task_id, queries, query_generation_source, query_generation_notice = storage.create_task(payload.topic)
        token, expires_at = storage.create_capture_token(task_id)
        storage.set_active_task(task_id=task_id, capture_token=token, token_expires_at=expires_at)
        logger.info(
            "Handled create task request",
            extra={"event_name": "mvp_create_task_request", "task_id": task_id, "detail": payload.topic.strip()},
        )
        return CreateTaskResponse(
            task_id=task_id,
            topic=payload.topic.strip(),
            expanded_queries=queries,
            query_generation_source=query_generation_source,
            query_generation_notice=query_generation_notice,
        )

    @app.get("/mvp/tasks/{task_id}", response_model=TaskSnapshotResponse)
    async def get_task(task_id: str) -> TaskSnapshotResponse:
        snapshot = storage.get_task_snapshot(task_id)
        if snapshot is None:
            logger.warning(
                "Task snapshot not found",
                extra={"event_name": "mvp_task_not_found", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found")
        logger.info(
            "Fetched task snapshot",
            extra={
                "event_name": "mvp_task_snapshot_fetched",
                "task_id": task_id,
                "item_count": snapshot.imported_item_count,
                "candidate_count": len(snapshot.candidates),
            },
        )
        return snapshot

    @app.post("/api/extension/capture", response_model=ExtensionCaptureResponse)
    async def ingest_extension_capture(
        payload: ExtensionCaptureRequest,
        x_capture_token: str = Header(default="", alias="X-Capture-Token"),
    ) -> ExtensionCaptureResponse:
        try:
            token_task_id = storage.validate_capture_token(x_capture_token)
        except InvalidCaptureToken as exc:
            logger.warning(
                "Rejected invalid extension capture token",
                extra={"event_name": "mvp_extension_capture_token_invalid", "detail": str(exc)},
            )
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if token_task_id != payload.task_id:
            raise HTTPException(status_code=403, detail="Capture token does not match task")
        try:
            return storage.ingest_extension_capture(
                task_id=payload.task_id,
                request_id=payload.request_id,
                page_type=payload.page_type,
                query_text=payload.query_text,
                items=payload.visible_items,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/mvp/tasks/{task_id}/manual-seeds", response_model=ManualSeedResponse)
    async def ingest_manual_seeds(task_id: str, payload: ManualSeedRequest) -> ManualSeedResponse:
        snapshot = storage.get_task_snapshot(task_id)
        if snapshot is None:
            logger.warning(
                "Manual seeds requested for missing task",
                extra={"event_name": "mvp_manual_seeds_task_missing", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found")
        imported_count, _ = storage.ingest_manual_text(task_id=task_id, text=payload.text)
        return ManualSeedResponse(task_id=task_id, imported_count=imported_count)

    @app.post("/mvp/tasks/{task_id}/queries", response_model=CustomQueryResponse)
    async def add_custom_queries(task_id: str, payload: CustomQueryRequest) -> CustomQueryResponse:
        snapshot = storage.get_task_snapshot(task_id)
        if snapshot is None:
            logger.warning(
                "Custom queries requested for missing task",
                extra={"event_name": "mvp_custom_queries_task_missing", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found")
        created_count, skipped_count = storage.add_custom_queries(task_id=task_id, text=payload.text)
        return CustomQueryResponse(task_id=task_id, created_count=created_count, skipped_count=skipped_count)

    @app.delete("/mvp/tasks/{task_id}/queries/{query_id}", response_model=DeleteCustomQueryResponse)
    async def delete_custom_query(task_id: str, query_id: str) -> DeleteCustomQueryResponse:
        snapshot = storage.get_task_snapshot(task_id)
        if snapshot is None:
            logger.warning(
                "Delete custom query requested for missing task",
                extra={"event_name": "mvp_delete_custom_query_task_missing", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            deleted = storage.delete_custom_query(task_id=task_id, query_id=query_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Query not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return DeleteCustomQueryResponse(task_id=task_id, deleted=deleted)

    @app.get("/mvp/tasks/{task_id}/hotspots", response_model=HotspotSnapshotResponse)
    async def get_hotspots(task_id: str) -> HotspotSnapshotResponse:
        snapshot = storage.get_hotspots(task_id)
        if snapshot is None:
            logger.warning(
                "Hotspot snapshot requested for missing task",
                extra={"event_name": "mvp_hotspots_task_missing", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found")
        return snapshot

    @app.post("/mvp/tasks/{task_id}/hotspots/refresh", response_model=HotspotSnapshotResponse)
    async def refresh_hotspots(task_id: str) -> HotspotSnapshotResponse:
        try:
            return await storage.refresh_hotspots(task_id)
        except KeyError as exc:
            logger.warning(
                "Hotspot refresh requested for missing task",
                extra={"event_name": "mvp_hotspots_refresh_task_missing", "task_id": task_id},
            )
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.get("/api/scraper/readiness", response_model=ScraperReadinessResponse)
    async def get_scraper_readiness(request: Request) -> ScraperReadinessResponse:
        """Detect whether the persistent Chrome profile exists and is logged in.

        Caches result for 60 s to avoid spawning a probe page on every poll cycle.
        Does not trigger login — returns logged_in=False when not authenticated.
        """
        runtime: ScraperRuntime = request.app.state.scraper_runtime
        cache: _ReadinessCache = request.app.state.readiness_cache

        cached = cache.get_fresh()
        if cached is not None:
            return cached

        profile_exists = runtime._profile_dir.exists() and any(runtime._profile_dir.iterdir())
        if not profile_exists:
            result = ScraperReadinessResponse(
                profile_exists=False,
                logged_in=False,
                last_checked_at=utc_now(),
                detail="profile dir is empty; complete first-time login per README",
            )
            cache.put(result)
            return result

        try:
            page = await runtime.acquire_page()
            try:
                await page.goto(
                    "https://www.xiaohongshu.com/",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                logged_in = await is_logged_in(page)
            finally:
                await page.close()
        except Exception as exc:  # noqa: BLE001
            result = ScraperReadinessResponse(
                profile_exists=True,
                logged_in=False,
                last_checked_at=utc_now(),
                detail=f"login probe failed: {exc}",
            )
            cache.put(result)
            return result

        result = ScraperReadinessResponse(
            profile_exists=True,
            logged_in=logged_in,
            last_checked_at=utc_now(),
            detail="ok" if logged_in else "not logged in; complete login per README",
        )
        cache.put(result)
        return result

    @app.post(
        "/api/tasks/{task_id}/auto-scrape",
        status_code=202,
        response_model=AutoScrapeResponse,
    )
    async def trigger_auto_scrape(
        task_id: str,
        payload: AutoScrapeRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> AutoScrapeResponse:
        task_storage: MVPStorage = request.app.state.mvp_storage
        registry: ScrapeStateRegistry = request.app.state.scrape_state_registry
        runtime: ScraperRuntime = request.app.state.scraper_runtime

        if not task_storage.task_exists(task_id):
            raise HTTPException(status_code=404, detail="Task not found")

        state = await registry.try_acquire(
            task_id=task_id,
            keyword=payload.keyword,
            scroll_total=payload.scroll_count,
        )
        if state is None:
            raise HTTPException(
                status_code=409,
                detail=f"another scrape already running (active: {registry.active_task_id})",
            )

        background_tasks.add_task(
            _run_scrape_background,
            runtime=runtime,
            registry=registry,
            storage=task_storage,
            task_id=task_id,
            keyword=payload.keyword,
            scroll_count=payload.scroll_count,
        )
        logger.info(
            "Auto-scrape accepted",
            extra={
                "event_name": "mvp_auto_scrape_accepted",
                "task_id": task_id,
                "keyword": payload.keyword,
                "scroll_count": payload.scroll_count,
            },
        )
        return AutoScrapeResponse(
            task_id=task_id,
            accepted=True,
            started_at=state.started_at,
        )

    @app.get("/api/tasks/{task_id}/scrape-status", response_model=ScrapeStatusResponse)
    async def get_scrape_status(task_id: str, request: Request) -> ScrapeStatusResponse:
        registry: ScrapeStateRegistry = request.app.state.scrape_state_registry
        state = registry.get(task_id)
        if state is None:
            raise HTTPException(status_code=404, detail="No scrape recorded for this task")
        return ScrapeStatusResponse(
            task_id=state.task_id,
            keyword=state.keyword,
            phase=state.progress.phase,
            scroll_index=state.progress.scroll_index,
            scroll_total=state.progress.scroll_total,
            items_count=state.progress.items_count,
            error_message=state.progress.error_message,
            started_at=state.started_at,
            finished_at=state.finished_at,
        )

    return app


app = create_app()
