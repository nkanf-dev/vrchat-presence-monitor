from __future__ import annotations

import logging
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Type, TypeVar

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
    Response,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from vrchat_monitor.vrchat import VRChatError, VRChatLoginResult

from .analytics import AnalyticsService, fetch_world_image
from .backup_json import (
    decode_backup as _decode_backup,
    read_backup_manifest as _read_backup_manifest,
)
from .hosted_collector import HostedCollectorManager
from .health import TenantHealthService
from .insights import InsightsService
from .organization import (
    AnnotationConflict,
    DashboardConflict,
    OrganizationConflict,
    OrganizationNotFound,
    OrganizationService,
)
from .search import SearchService
from .schemas import (
    AnnotationRequest,
    BootstrapRequest,
    DashboardPutRequest,
    DashboardQueryRequest,
    DashboardSharePutRequest,
    DashboardShareUnlockRequest,
    LoginRequest,
    PreferenceRequest,
    TagRequest,
    TelemetryRequest,
    VRChatLoginRequest,
    VRChatTwoFactorRequest,
)
from .security import (
    LoginRateLimiter,
    RequestRateLimiter,
    bearer,
    browser_token,
    clear_browser_cookies,
    clear_pending_cookie,
    client_address,
    constant_time_secret,
    pending_token,
    request_is_secure,
    require_same_origin,
    set_browser_cookie,
    set_pending_cookie,
)
from .session_crypto import SessionCipher
from .settings import Settings
from .storage import Store
from .vrchat_auth import VRChatAuthService
from .worlds import DiscoveryService, WorldResolver, WorldService


LOGGER = logging.getLogger("presence_monitor.hosted")
Model = TypeVar("Model", bound=BaseModel)
Result = TypeVar("Result")


@dataclass(frozen=True)
class Authenticated:
    token: str
    row: dict[str, Any]
    migrated: bool = False


async def _read_bytes(request: Request, maximum: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > maximum:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="request too large")
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid content length") from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="request too large")
    return bytes(body)


async def _read_model(request: Request, model: Type[Model], maximum: int) -> Model:
    raw = await _read_bytes(request, maximum)
    try:
        return await run_in_threadpool(model.model_validate_json, raw)
    except ValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid request") from error


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    session_cipher = SessionCipher(config.vrchat_session_key) if config.hosted_vrchat_login else None
    database = store or Store(
        str(config.data_dir / "hosted.sqlite3"),
        friend_limit=config.tenant_friend_limit,
        event_limit=config.tenant_event_limit,
        max_backup_bytes=config.max_import_bytes,
        session_cipher=session_cipher,
    )
    if config.hosted_vrchat_login and database.session_cipher is None:
        database.session_cipher = session_cipher
    proxy_overrides = dict(
        item.split("=", 1)
        for item in config.hosted_vrchat_proxy_overrides.split(",")
        if "=" in item and all(part.strip() for part in item.split("=", 1))
    )
    vrchat_auth = (
        VRChatAuthService(
            session_cipher,
            proxy_url=config.hosted_vrchat_login_proxy,
        )
        if session_cipher
        else None
    )
    analytics = AnalyticsService(database)
    organization = OrganizationService(database)
    search = SearchService(database)
    insights = InsightsService(database)
    tenant_health = TenantHealthService(database)
    hosted_collector = (
        HostedCollectorManager(
            database,
            poll_seconds=config.hosted_collector_poll_seconds,
            concurrency=config.hosted_collector_concurrency,
            max_backoff_seconds=config.hosted_collector_max_backoff_seconds,
            proxy_overrides=proxy_overrides,
        )
        if config.hosted_vrchat_login
        else None
    )

    def fetch_world(tenant_id: str, world_id: str) -> dict[str, Any]:
        if hosted_collector is None:
            raise VRChatError("世界信息暂时不可用", 503)
        return hosted_collector.fetch_world(tenant_id, world_id)

    world_resolver = WorldResolver(
        database,
        fetch_world,
        max_backoff_seconds=config.hosted_collector_max_backoff_seconds,
    )
    worlds = WorldService(database, world_resolver)
    discovery = DiscoveryService(database, world_resolver)
    if hosted_collector is not None:
        def observe_worlds(tenant_id: str, world_ids: list[str]) -> None:
            for world_id in world_ids:
                world_resolver.enqueue(tenant_id, world_id)

        hosted_collector.set_world_observer(observe_worlds)
    limiter = LoginRateLimiter(config.login_attempts, config.login_window_seconds)
    collector_limiter = RequestRateLimiter(config.collector_requests_per_minute, 60)
    import_limiter = RequestRateLimiter(config.import_requests, config.import_window_seconds)
    share_unlock_limiter = LoginRateLimiter(5, 15 * 60)

    def device_class(request: Request) -> str:
        value = str(request.headers.get("user-agent") or "").lower()
        if "ipad" in value or "tablet" in value:
            return "tablet"
        if "mobile" in value or "android" in value or "iphone" in value:
            return "mobile"
        return "desktop" if value else "unknown"

    def panel_data(tenant_id: str, panel: dict[str, Any], global_range_days: int) -> dict[str, Any]:
        kind = str(panel["kind"])
        range_days = int(panel.get("range_days") or global_range_days)
        friend_ids = [str(value) for value in panel.get("friend_ids", [])]
        statuses = [str(value) for value in panel.get("statuses", [])]
        platforms = [str(value) for value in panel.get("platforms", [])]
        include_self = bool(panel.get("include_self", True))
        limit = int(panel.get("limit") or 10)
        sort_direction = str(panel.get("sort_direction") or "auto")
        if kind in {"online-now", "tracked-count", "status-breakdown", "platform-breakdown"}:
            current_statuses = statuses
            if kind == "platform-breakdown" and not current_statuses:
                current_statuses = ["active", "join me", "ask me", "busy"]
            current = database.dashboard_current(
                tenant_id,
                friend_ids=friend_ids,
                statuses=current_statuses,
                platforms=platforms,
                include_self=include_self,
            )
            if kind in {"online-now", "tracked-count"}:
                value = current["online_count"] if kind == "online-now" else current["tracked_count"]
                detail = f"{current['tracked_count']} 位筛选对象" if kind == "online-now" else f"{current['online_count']} 位当前在线"
                ratio = current["online_count"] / current["tracked_count"] if current["tracked_count"] else 0.0
                return {"kind": kind, "value": value, "detail": detail, "ratio": ratio}
            counts = current["status_counts"] if kind == "status-breakdown" else current["platform_counts"]
            items = [{"name": name, "value": value} for name, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
            if sort_direction == "asc":
                items.reverse()
            return {"kind": kind, "items": items}
        if kind in {"online-ranking", "daily-changes"}:
            result = analytics.stats(tenant_id, range_days)
            if kind == "daily-changes":
                items = list(result["daily_changes"])
                if sort_direction == "desc":
                    items.reverse()
                return {"kind": kind, "items": items}
            allowed = set(friend_ids)
            items = [
                item for item in result["online_hours_all"]
                if (include_self or not item.get("is_self"))
                and (not allowed or item["id"] in allowed)
            ]
            if sort_direction == "asc":
                items.sort(key=lambda item: (float(item.get("seconds") or 0), str(item.get("name") or "")))
            return {"kind": kind, "items": items[:limit]}
        if kind == "friend-heatmap":
            today = datetime.now(timezone.utc).date()
            start = today - timedelta(days=max(0, range_days - 1))
            result = analytics.presence_overview(
                tenant_id, today.isoformat(), range_days, start.isoformat(), today.isoformat()
            )
            allowed = set(friend_ids)
            rows = [
                row for row in result["heatmap"]
                if (include_self or not row["is_self"]) and (not allowed or row["id"] in allowed)
            ]
            rows.sort(key=lambda row: -sum(float(cell.get("online_minutes") or 0) for cell in row["cells"]))
            if sort_direction == "asc":
                rows.reverse()
            return {"kind": kind, "rows": rows[:limit]}
        if kind == "world-ranking":
            discovery_days = max(1, min(range_days, 730))
            world_sort = str(panel.get("world_sort") or "people")
            result = discovery.discover(
                tenant_id,
                discovery_days,
                friend_ids=friend_ids,
                world_tag=str(panel.get("world_tag") or ""),
                world_ids=[str(value) for value in panel.get("world_ids", [])],
                hot_sort=world_sort,
                sort_direction="asc" if sort_direction == "asc" else "desc",
                include_self=include_self,
                limit=limit,
                offset=0,
                allow_custom_range=True,
            )
            return {"kind": kind, "items": result["hot"]}
        if kind == "collection-coverage":
            today = datetime.now(timezone.utc).date()
            start = today - timedelta(days=max(0, range_days - 1))
            result = analytics.coverage_overview(tenant_id, start.isoformat(), today.isoformat())
            return {
                "kind": kind,
                "ratio": result["ratio"],
                "observed_minutes": result["observed_minutes"],
                "expected_minutes": result["expected_minutes"],
            }
        raise ValueError("不支持的仪表盘图表")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.cleanup_expired_sessions()
        if hosted_collector:
            hosted_collector.start()
        world_resolver.start()
        try:
            yield
        finally:
            world_resolver.stop()
            if hosted_collector:
                hosted_collector.stop()

    app = FastAPI(
        title="Presence Monitor Hosted API",
        version="0.3.0-beta.7",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.store = database
    app.state.analytics = analytics
    app.state.organization = organization
    app.state.search = search
    app.state.insights = insights
    app.state.tenant_health = tenant_health
    app.state.world_resolver = world_resolver
    app.state.worlds = worlds
    app.state.discovery = discovery
    app.state.hosted_collector = hosted_collector

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": str(error.detail)},
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid request"})

    @app.exception_handler(AnnotationConflict)
    async def annotation_conflict(_: Request, error: AnnotationConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"server": error.server})

    @app.exception_handler(DashboardConflict)
    async def dashboard_conflict(_: Request, error: DashboardConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"server": error.server})

    @app.exception_handler(OrganizationNotFound)
    async def organization_not_found(_: Request, __: OrganizationNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not found"})

    @app.exception_handler(OrganizationConflict)
    async def organization_conflict(_: Request, __: OrganizationConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "conflict"})

    @app.middleware("http")
    async def product_headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "style-src-attr 'unsafe-inline'; "
            "img-src 'self' data: https://*.vrchat.cloud https://*.vrcdn.cloud; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path == "/v1/world-image" or request.url.path in {
            "/icon.svg",
            "/favicon-32.png",
            "/apple-touch-icon.png",
            "/icon-192.png",
            "/icon-512.png",
            "/manifest.webmanifest",
        }:
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        if request_is_secure(request, config):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        LOGGER.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            round((time.monotonic() - started) * 1000),
            request_id,
        )
        return response

    def viewer(request: Request, response: Response) -> Authenticated:
        token, migrated = browser_token(request)
        row = database.auth(token, "viewer") if token else None
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        if migrated:
            set_browser_cookie(response, request, config, token)
        return Authenticated(token=token, row=row, migrated=migrated)

    def collector(request: Request) -> Authenticated:
        token = bearer(request)
        row = database.auth(token, "collector") if token else None
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        return Authenticated(token=token, row=row)

    def bootstrap_admin(request: Request) -> None:
        if not constant_time_secret(request.headers.get("x-bootstrap-token", ""), config.bootstrap_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    async def admin_call(
        function: Callable[..., Result], *args: Any, **kwargs: Any
    ) -> Result:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except KeyError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from error

    async def organization_call(
        function: Callable[..., Result], *args: Any, **kwargs: Any
    ) -> Result:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.get("/healthz")
    @app.get("/livez")
    def live() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    def ready() -> JSONResponse:
        enough_space = shutil.disk_usage(config.data_dir).free >= config.minimum_free_bytes
        if not database.ready() or not enough_space:
            return JSONResponse(status_code=503, content={"ok": False})
        return JSONResponse(content={"ok": True})

    @app.post("/v1/bootstrap", status_code=201)
    async def bootstrap(request: Request, _: None = Depends(bootstrap_admin)) -> dict[str, str]:
        payload = await _read_model(request, BootstrapRequest, 64 * 1024)
        return await run_in_threadpool(
            database.bootstrap,
            payload.tenant_name.strip(),
            payload.collector_name.strip(),
        )

    @app.post("/v1/admin/tenants/{tenant_id}/access-code/rotate")
    async def rotate_access_code(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        return await admin_call(database.rotate_access_code, tenant_id)

    @app.delete("/v1/admin/tenants/{tenant_id}/access-code")
    async def revoke_access_codes(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        revoked = await admin_call(database.revoke_access_codes, tenant_id)
        return {"ok": True, "tenant_id": tenant_id, "revoked_count": revoked}

    @app.post("/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token/rotate")
    async def rotate_collector_token(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        collector_id: str = PathParam(min_length=1, max_length=128),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, str]:
        return await admin_call(database.rotate_collector_token, tenant_id, collector_id)

    @app.delete("/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token")
    async def revoke_collector_token(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        collector_id: str = PathParam(min_length=1, max_length=128),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        revoked = await admin_call(database.revoke_collector, tenant_id, collector_id)
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "collector_id": collector_id,
            "revoked": revoked,
        }

    @app.post("/v1/admin/tenants/{tenant_id}/viewer-sessions/revoke-all")
    async def revoke_all_viewer_sessions(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        revoked = await admin_call(database.revoke_all_viewer_sessions, tenant_id)
        return {"ok": True, "tenant_id": tenant_id, "revoked_count": revoked}

    @app.get("/v1/admin/tenants/{tenant_id}/security-audit")
    async def security_audit(
        tenant_id: str = PathParam(min_length=1, max_length=128),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        return await admin_call(database.security_audit, tenant_id, limit=limit, offset=offset)

    @app.get("/v1/admin/health")
    async def tenant_health_overview(
        _: None = Depends(bootstrap_admin),
    ) -> dict[str, Any]:
        return await run_in_threadpool(tenant_health.list)

    @app.post("/v1/login")
    async def login(request: Request, response: Response) -> dict[str, Any]:
        require_same_origin(request)
        address = client_address(request, config)
        if not limiter.allowed(address):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="尝试次数过多，请稍后重试",
                headers={"Retry-After": str(config.login_window_seconds)},
            )
        payload = await _read_model(request, LoginRequest, 16 * 1024)
        session = await run_in_threadpool(database.exchange_access_code, payload.access_code, config.session_days)
        if not session:
            limiter.fail(address)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问码无效")
        limiter.clear(address)
        set_browser_cookie(response, request, config, session["session_token"])
        identity = await run_in_threadpool(database.viewer_identity, session["session_token"])
        return {"ok": True, "user": identity, "expires_at": session["expires_at"]}

    async def finish_vrchat_login(
        result: VRChatLoginResult,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        user = result.user or {}
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise HTTPException(status_code=502, detail="VRChat 登录响应无效")
        display_name = str(user.get("displayName") or user.get("username") or user_id)
        account = await run_in_threadpool(
            database.connect_vrchat_account,
            user_id,
            display_name,
            result.cookie,
        )
        session = await run_in_threadpool(
            database.create_viewer_session,
            account["tenant_id"],
            config.session_days,
        )
        set_browser_cookie(response, request, config, session["session_token"])
        clear_pending_cookie(response)
        if hosted_collector:
            hosted_collector.wake(account["tenant_id"])
        identity = await run_in_threadpool(
            database.viewer_identity, session["session_token"]
        )
        return {
            "ok": True,
            "requires_2fa": False,
            "user": identity,
            "expires_at": session["expires_at"],
        }

    @app.post("/v1/vrchat/login")
    async def vrchat_login(request: Request, response: Response) -> dict[str, Any]:
        if vrchat_auth is None:
            raise HTTPException(status_code=503, detail="VRChat 登录暂不可用")
        require_same_origin(request)
        address = client_address(request, config)
        if not limiter.allowed(address):
            raise HTTPException(
                status_code=429,
                detail="尝试次数较多，请稍后再试",
                headers={"Retry-After": str(config.login_window_seconds)},
            )
        payload = await _read_model(request, VRChatLoginRequest, 8 * 1024)
        try:
            pending_id, result = await run_in_threadpool(
                vrchat_auth.begin,
                payload.username.strip(),
                payload.password.get_secret_value(),
            )
        except VRChatError as error:
            if error.status == 401:
                limiter.fail(address)
                raise HTTPException(status_code=401, detail="账号或密码不正确") from error
            if error.status == 429:
                raise HTTPException(
                    status_code=429,
                    detail="VRChat 请求较多，请稍后再试",
                    headers={"Retry-After": str(int(error.retry_after or 60))},
                ) from error
            raise HTTPException(status_code=502, detail="暂时无法连接 VRChat") from error
        limiter.clear(address)
        if result.requires_2fa and pending_id:
            set_pending_cookie(response, request, config, pending_id)
            return {
                "ok": True,
                "requires_2fa": True,
                "methods": list(result.methods),
            }
        return await finish_vrchat_login(result, request, response)

    @app.post("/v1/vrchat/2fa")
    async def vrchat_two_factor(request: Request, response: Response) -> dict[str, Any]:
        if vrchat_auth is None:
            raise HTTPException(status_code=503, detail="VRChat 登录暂不可用")
        require_same_origin(request)
        pending_id = pending_token(request)
        if not pending_id:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        payload = await _read_model(request, VRChatTwoFactorRequest, 4 * 1024)
        try:
            result = await run_in_threadpool(vrchat_auth.complete, pending_id, payload.code.strip())
        except ValueError as error:
            clear_pending_cookie(response)
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录") from error
        except VRChatError as error:
            if error.status == 401:
                raise HTTPException(status_code=401, detail="验证码不正确") from error
            raise HTTPException(status_code=502, detail="暂时无法连接 VRChat") from error
        return await finish_vrchat_login(result, request, response)

    @app.get("/v1/me")
    def me(auth: Authenticated = Depends(viewer)) -> dict[str, Any]:
        identity = database.viewer_identity(auth.token)
        if not identity:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        return {"authenticated": True, "user": identity, "migrated": auth.migrated}

    @app.post("/v1/logout")
    def logout(request: Request, response: Response, auth: Authenticated = Depends(viewer)) -> dict[str, bool]:
        require_same_origin(request)
        if not database.revoke_viewer(auth.token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        clear_browser_cookies(response)
        return {"ok": True}

    @app.post("/v1/vrchat/disconnect")
    def disconnect_vrchat(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, bool]:
        require_same_origin(request)
        disconnected = database.disconnect_vrchat_account(auth.row["tenant_id"])
        if hosted_collector:
            hosted_collector.disconnect(auth.row["tenant_id"])
        return {"ok": True, "disconnected": disconnected}

    @app.get("/v1/overview")
    def overview(auth: Authenticated = Depends(viewer)) -> dict[str, Any]:
        return database.overview(auth.row["tenant_id"])

    @app.get("/v1/analytics/stats")
    def analytics_stats(
        days: int = Query(default=30, ge=1, le=90),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return analytics.stats(auth.row["tenant_id"], days)

    @app.get("/v1/analytics/presence")
    def analytics_presence(
        day: str | None = Query(default=None, max_length=10),
        days: int = Query(default=30, ge=1, le=90),
        heatmap_from: str | None = Query(default=None, max_length=10),
        heatmap_to: str | None = Query(default=None, max_length=10),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return analytics.presence_overview(
                auth.row["tenant_id"], day, days, heatmap_from, heatmap_to
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/analytics/worlds")
    def analytics_worlds(
        day: str | None = Query(default=None, max_length=10),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return analytics.world_presence_overview(auth.row["tenant_id"], day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/coverage")
    def analytics_coverage(
        range_from: str | None = Query(default=None, alias="from", max_length=10),
        range_to: str | None = Query(default=None, alias="to", max_length=10),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return analytics.coverage_overview(
                auth.row["tenant_id"], range_from, range_to
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/worlds/{world_id}")
    def world_info(
        world_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return worlds.detail(auth.row["tenant_id"], world_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/world-library")
    def world_library(
        q: str = Query(default="", max_length=160),
        author: str = Query(default="", max_length=160),
        friend_id: str = Query(default="", max_length=128),
        world_tag: str = Query(default="", max_length=160),
        cursor: str = Query(default="", max_length=256),
        offset: int | None = Query(default=None, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return worlds.library(
                auth.row["tenant_id"],
                query=q,
                author=author,
                friend_id=friend_id,
                world_tag=world_tag,
                cursor=cursor,
                offset=offset,
                limit=limit,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/world-tags")
    def world_tags(
        friend_id: str = Query(default="", max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> list[dict[str, Any]]:
        try:
            return worlds.tags(auth.row["tenant_id"], friend_id=friend_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="not found") from error

    @app.get("/v1/discovery/worlds")
    def discover_worlds(
        days: int = Query(default=7),
        friend_id: str = Query(default="", max_length=128),
        world_tag: str = Query(default="", max_length=160),
        include_self: bool = Query(default=True),
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return discovery.discover(
                auth.row["tenant_id"],
                days,
                friend_id=friend_id,
                world_tag=world_tag,
                include_self=include_self,
                limit=limit,
                offset=offset,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/world-image")
    def world_image(
        url: str = Query(min_length=1, max_length=4096),
        _: Authenticated = Depends(viewer),
    ) -> Response:
        try:
            body, content_type = fetch_world_image(url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except VRChatError as error:
            response_status = error.status if error.status in {413, 415} else 502
            raise HTTPException(status_code=response_status, detail=str(error)) from error
        return Response(
            content=body,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    @app.post("/v1/sync")
    def sync_now(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, bool]:
        require_same_origin(request)
        if hosted_collector is None:
            raise HTTPException(status_code=503, detail="托管采集暂不可用")
        queued = hosted_collector.wake(auth.row["tenant_id"])
        if not queued:
            raise HTTPException(status_code=409, detail="尚未连接 VRChat")
        return {"ok": True, "queued": True}

    @app.get("/v1/friends")
    def friends(
        q: str = Query(default="", max_length=120),
        status_filter: str = Query(default="", alias="status", max_length=40),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return database.friends_page(
            auth.row["tenant_id"], query=q, status=status_filter, limit=limit, offset=offset
        )

    @app.get("/v1/search")
    def global_search(
        q: str = Query(min_length=1, max_length=160),
        limit: int = Query(default=8, ge=1, le=20),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return search.search(auth.row["tenant_id"], q, limit)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/friends/{friend_id}/annotation")
    async def friend_annotation(
        friend_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            organization.get_annotation,
            auth.row["tenant_id"],
            friend_id,
        )

    @app.put("/v1/friends/{friend_id}/annotation")
    async def update_friend_annotation(
        request: Request,
        friend_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, AnnotationRequest, 32 * 1024)
        return await organization_call(
            organization.put_annotation,
            auth.row["tenant_id"],
            friend_id,
            payload.note,
            payload.pinned,
            payload.revision,
        )

    @app.get("/v1/friends/{friend_id}/insights")
    def friend_insights(
        friend_id: str = PathParam(min_length=1, max_length=128),
        range_from: str = Query(alias="from", min_length=10, max_length=10),
        range_to: str = Query(alias="to", min_length=10, max_length=10),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        try:
            return insights.friend(
                auth.row["tenant_id"], friend_id, range_from, range_to
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/tags")
    async def tags(auth: Authenticated = Depends(viewer)) -> list[dict[str, Any]]:
        return await run_in_threadpool(
            organization.list_tags,
            auth.row["tenant_id"],
        )

    @app.post("/v1/tags", status_code=201)
    async def create_tag(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, TagRequest, 8 * 1024)
        return await organization_call(
            organization.create_tag,
            auth.row["tenant_id"],
            payload.name,
            payload.color,
        )

    @app.put("/v1/tags/{tag_id}")
    async def update_tag(
        request: Request,
        tag_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, TagRequest, 8 * 1024)
        return await organization_call(
            organization.update_tag,
            auth.row["tenant_id"],
            tag_id,
            payload.name,
            payload.color,
        )

    @app.delete("/v1/tags/{tag_id}")
    async def delete_tag(
        request: Request,
        tag_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, bool]:
        require_same_origin(request)
        await run_in_threadpool(
            organization.delete_tag,
            auth.row["tenant_id"],
            tag_id,
        )
        return {"ok": True}

    @app.put("/v1/friends/{friend_id}/tags/{tag_id}")
    async def assign_friend_tag(
        request: Request,
        friend_id: str = PathParam(min_length=1, max_length=128),
        tag_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        return await run_in_threadpool(
            organization.assign_tag,
            auth.row["tenant_id"],
            friend_id,
            tag_id,
        )

    @app.delete("/v1/friends/{friend_id}/tags/{tag_id}")
    async def unassign_friend_tag(
        request: Request,
        friend_id: str = PathParam(min_length=1, max_length=128),
        tag_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        return await run_in_threadpool(
            organization.unassign_tag,
            auth.row["tenant_id"],
            friend_id,
            tag_id,
        )

    @app.get("/v1/preferences")
    async def preferences(
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, str]:
        return await run_in_threadpool(
            organization.get_preferences,
            auth.row["tenant_id"],
        )

    @app.put("/v1/preferences")
    async def update_preferences(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, str]:
        require_same_origin(request)
        payload = await _read_model(request, PreferenceRequest, 8 * 1024)
        return await organization_call(
            organization.put_preferences,
            auth.row["tenant_id"],
            payload.timezone,
        )

    @app.get("/v1/dashboard")
    async def dashboard(
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            organization.get_dashboard,
            auth.row["tenant_id"],
        )

    @app.put("/v1/dashboard")
    async def update_dashboard(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, DashboardPutRequest, 64 * 1024)
        return await organization_call(
            organization.put_dashboard,
            auth.row["tenant_id"],
            payload.document.model_dump(mode="json"),
            payload.revision,
        )

    @app.post("/v1/dashboard/query")
    async def dashboard_query(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, DashboardQueryRequest, 32 * 1024)
        try:
            return await run_in_threadpool(
                panel_data,
                auth.row["tenant_id"],
                payload.panel.model_dump(mode="json"),
                payload.global_range_days,
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/v1/dashboard/share")
    async def dashboard_share(auth: Authenticated = Depends(viewer)) -> dict[str, Any]:
        return await run_in_threadpool(organization.get_dashboard_share, auth.row["tenant_id"])

    @app.put("/v1/dashboard/share")
    async def publish_dashboard_share(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        payload = await _read_model(request, DashboardSharePutRequest, 16 * 1024)
        return await organization_call(
            organization.publish_dashboard_share,
            auth.row["tenant_id"],
            payload.password,
            payload.appearance.model_dump(mode="json"),
        )

    @app.delete("/v1/dashboard/share")
    async def revoke_dashboard_share(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        revoked = await run_in_threadpool(organization.revoke_dashboard_share, auth.row["tenant_id"])
        return {"ok": True, "revoked": revoked}

    @app.get("/v1/dashboard/share/audit")
    async def dashboard_share_audit(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            organization.dashboard_share_audit,
            auth.row["tenant_id"],
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/public/dashboard/{share_id}")
    async def public_dashboard(
        request: Request,
        share_id: str = PathParam(min_length=16, max_length=96),
    ) -> dict[str, Any]:
        packed = str(request.cookies.get("presence_share") or "")
        token = packed.split(".", 1)[1] if packed.startswith(f"{share_id}.") else ""
        try:
            shared = await run_in_threadpool(
                organization.public_dashboard_share,
                share_id,
                token,
                client_address(request, config),
                device_class(request),
            )
        except OrganizationNotFound as error:
            raise HTTPException(status_code=404, detail="共享仪表盘不存在") from error
        if shared.get("locked"):
            return shared
        tenant_id = str(shared.pop("tenant_id"))
        document = shared["document"]
        data: dict[str, Any] = {}
        for panel in document.get("panels", []):
            try:
                data[str(panel["id"])] = await run_in_threadpool(
                    panel_data, tenant_id, panel, int(document.get("range_days") or 7)
                )
            except (KeyError, ValueError):
                data[str(panel.get("id") or "")] = {"kind": str(panel.get("kind") or ""), "error": "这张图表暂时无法载入"}
            except Exception:
                LOGGER.exception("public dashboard panel query failed")
                data[str(panel.get("id") or "")] = {"kind": str(panel.get("kind") or ""), "error": "这张图表暂时无法载入"}
        public_document = {
            **document,
            "panels": [
                {
                    key: value for key, value in panel.items()
                    if key not in {"friend_ids", "statuses", "platforms", "world_ids", "world_tag"}
                }
                for panel in document.get("panels", [])
            ],
        }
        return {**shared, "document": public_document, "data": data}

    @app.post("/v1/public/dashboard/{share_id}/unlock")
    async def unlock_public_dashboard(
        request: Request,
        response: Response,
        share_id: str = PathParam(min_length=16, max_length=96),
    ) -> dict[str, bool]:
        require_same_origin(request)
        address = client_address(request, config)
        limiter_key = f"{share_id}:{address}"
        if not share_unlock_limiter.allowed(limiter_key):
            raise HTTPException(
                status_code=429, detail="尝试次数过多，请稍后再试", headers={"Retry-After": "900"}
            )
        payload = await _read_model(request, DashboardShareUnlockRequest, 2048)
        try:
            token = await run_in_threadpool(
                organization.unlock_dashboard_share,
                share_id,
                payload.password,
                address,
                device_class(request),
            )
        except OrganizationNotFound as error:
            raise HTTPException(status_code=404, detail="共享仪表盘不存在") from error
        if token is None:
            share_unlock_limiter.fail(limiter_key)
            raise HTTPException(status_code=401, detail="密码不正确")
        share_unlock_limiter.clear(limiter_key)
        secure = request_is_secure(request, config)
        response.set_cookie(
            key="presence_share",
            value=f"{share_id}.{token}",
            max_age=24 * 60 * 60,
            path="/",
            secure=secure,
            httponly=True,
            samesite="lax",
        )
        return {"ok": True}

    @app.get("/v1/events")
    def events(
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return database.events_page(auth.row["tenant_id"], query=q, limit=limit, offset=offset)

    @app.get("/v1/data")
    def legacy_data(
        limit: int = Query(default=1000, ge=0, le=10000),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        return database.data(auth.row["tenant_id"], limit)

    @app.get("/v1/capabilities")
    def capabilities(_: Authenticated = Depends(viewer)) -> dict[str, int]:
        return {
            "max_import_bytes": config.max_import_bytes,
            "max_import_expanded_bytes": config.max_import_expanded_bytes,
            "max_source_expanded_bytes": config.max_source_expanded_bytes,
        }

    @app.get("/v1/export.json")
    def export_json(
        include_raw: bool = Query(default=True),
        auth: Authenticated = Depends(viewer),
    ) -> StreamingResponse:
        filename = (
            f"presence-monitor-backup-{time.strftime('%Y-%m-%d', time.gmtime())}.json.gz"
        )
        return StreamingResponse(
            database.stream_export_v3(auth.row["tenant_id"], include_raw),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/v1/import.json")
    async def import_json(
        request: Request,
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        require_same_origin(request)
        if not import_limiter.consume(str(auth.row["tenant_id"])):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="导入过于频繁，请稍后重试",
                headers={"Retry-After": str(config.import_window_seconds)},
            )
        raw = await _read_bytes(request, config.max_import_bytes)
        try:
            manifest = await run_in_threadpool(
                _read_backup_manifest,
                raw,
                config.max_import_expanded_bytes,
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
        try:
            if (
                manifest.get("format") == "vrchat-monitor-hosted-backup"
                and manifest.get("version") == 3
                and not isinstance(manifest.get("version"), bool)
            ):
                imported = await run_in_threadpool(
                    database.import_v3,
                    auth.row["tenant_id"],
                    raw,
                    config.max_import_expanded_bytes,
                )
            else:
                payload = await run_in_threadpool(
                    _decode_backup,
                    raw,
                    config.max_import_expanded_bytes,
                )
                imported = await run_in_threadpool(
                    database.import_json,
                    auth.row["tenant_id"],
                    payload,
                )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
        return {"ok": True, "imported": imported}

    @app.post("/v1/telemetry")
    async def telemetry(request: Request, auth: Authenticated = Depends(collector)) -> dict[str, int]:
        if not collector_limiter.consume(str(auth.row["id"])):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="上传过于频繁，请稍后重试",
                headers={"Retry-After": "60"},
            )
        payload = await _read_model(request, TelemetryRequest, config.max_telemetry_bytes)
        try:
            if payload.schema_version == 2:
                observation = payload.observation
                if observation is None:  # Kept explicit for type checkers and audits.
                    raise ValueError("schema_version 2 requires observation")
                return await run_in_threadpool(
                    database.ingest_authoritative_snapshot,
                    auth.row["tenant_id"],
                    auth.row["id"],
                    [friend.model_dump() for friend in payload.friends],
                    [event.model_dump() for event in payload.events],
                    source="external-collector",
                    observed_at=observation.observed_at,
                    expected_interval_seconds=observation.expected_interval_seconds,
                )
            return await run_in_threadpool(
                database.ingest,
                auth.row["tenant_id"],
                auth.row["id"],
                [friend.model_dump() for friend in payload.friends],
                [event.model_dump() for event in payload.events],
                "local-bridge",
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def unknown_api(path: str) -> None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    assets = config.static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/icon.svg")
    @app.get("/favicon-32.png")
    @app.get("/apple-touch-icon.png")
    @app.get("/icon-192.png")
    @app.get("/icon-512.png")
    @app.get("/manifest.webmanifest")
    def root_static_asset(request: Request):
        asset = config.static_dir / request.url.path.removeprefix("/")
        if not asset.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        return FileResponse(asset)

    @app.get("/")
    @app.get("/{path:path}")
    def spa(path: str = ""):
        if path.startswith("v1/") or path in {"livez", "readyz", "healthz"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        index = config.static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="frontend unavailable")
        return FileResponse(index, media_type="text/html")

    return app


def main() -> None:
    config = Settings.from_env()
    if not config.bootstrap_token:
        raise SystemExit("BOOTSTRAP_TOKEN must be set")
    uvicorn.run(
        "server.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(__import__("os").environ.get("PORT", "8080")),
        proxy_headers=config.trust_proxy_headers,
        access_log=False,
    )


if __name__ == "__main__":
    main()
    pending_token,
