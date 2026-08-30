from __future__ import annotations

import gzip
import json
import logging
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from vrchat_monitor.vrchat import VRChatError, VRChatLoginResult

from .analytics import AnalyticsService, fetch_world_image
from .backup_json import decode_backup as _decode_backup
from .hosted_collector import HostedCollectorManager
from .schemas import (
    BootstrapRequest,
    LoginRequest,
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
from .storage import BACKUP_COMPRESSION_MARGIN, Store
from .vrchat_auth import VRChatAuthService


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
    vrchat_auth = VRChatAuthService(session_cipher) if session_cipher else None
    analytics = AnalyticsService(database)
    hosted_collector = (
        HostedCollectorManager(
            database,
            poll_seconds=config.hosted_collector_poll_seconds,
            concurrency=config.hosted_collector_concurrency,
            max_backoff_seconds=config.hosted_collector_max_backoff_seconds,
        )
        if config.hosted_vrchat_login
        else None
    )
    limiter = LoginRateLimiter(config.login_attempts, config.login_window_seconds)
    collector_limiter = RequestRateLimiter(config.collector_requests_per_minute, 60)
    import_limiter = RequestRateLimiter(config.import_requests, config.import_window_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.cleanup_expired_sessions()
        if hosted_collector:
            hosted_collector.start()
        try:
            yield
        finally:
            if hosted_collector:
                hosted_collector.stop()

    app = FastAPI(
        title="Presence Monitor Hosted API",
        version="0.2.0-beta.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.store = database
    app.state.analytics = analytics
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
            "img-src 'self' data: https://*.vrchat.cloud https://*.vrcdn.cloud; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path == "/v1/world-image":
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

    @app.get("/v1/worlds/{world_id}")
    def world_info(
        world_id: str = PathParam(min_length=1, max_length=128),
        auth: Authenticated = Depends(viewer),
    ) -> dict[str, Any]:
        if hosted_collector is None:
            cached = database.world_cache_get(world_id)
            if cached is not None:
                return cached
            raise HTTPException(status_code=503, detail="世界解析暂不可用")
        try:
            return hosted_collector.world_info(auth.row["tenant_id"], world_id)
        except VRChatError as error:
            response_status = error.status if error.status in {400, 401, 404, 429} else 502
            headers = (
                {"Retry-After": str(max(1, int(error.retry_after or 60)))}
                if response_status == 429
                else None
            )
            raise HTTPException(
                status_code=response_status,
                detail=str(error),
                headers=headers,
            ) from error

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
    def export_json(auth: Authenticated = Depends(viewer)) -> Response:
        payload = database.export_json(auth.row["tenant_id"])
        filename = f"presence-monitor-backup-{time.strftime('%Y-%m-%d', time.gmtime())}.json"
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= config.max_import_bytes:
            return Response(
                content=encoded,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if len(encoded) > config.max_import_expanded_bytes:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "当前数据超过服务器直接恢复上限；请使用部署者的 SQLite/R2 备份，"
                    "或提高 MAX_IMPORT_EXPANDED_BYTES 后从直连端点导出"
                ),
            )
        compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
        if len(compressed) > config.max_import_bytes - BACKUP_COMPRESSION_MARGIN:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "压缩备份仍超过上传上限；请使用部署者的 SQLite/R2 备份，"
                    "或提高容量后从直连端点导出"
                ),
            )
        return Response(
            content=compressed,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}.gz"'},
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
            payload = await run_in_threadpool(
                _decode_backup,
                raw,
                config.max_import_expanded_bytes,
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
        try:
            imported = await run_in_threadpool(database.import_json, auth.row["tenant_id"], payload)
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
