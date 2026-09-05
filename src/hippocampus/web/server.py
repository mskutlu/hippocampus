"""FastAPI backend for the Hippocampus web UI.

Mounts static files, exposes a thin JSON API around
`hippocampus.mcp.tools`, and serves a tiny SPA.

Runs on 127.0.0.1 by default. Same-origin + a random token in the
`X-Hippo-Token` header guards mutations. This is a defence-in-depth
measure — it does not replace binding to loopback.
"""

from __future__ import annotations

import logging
import secrets
import webbrowser
from datetime import date
from pathlib import Path
from typing import Any, Literal

try:
    from fastapi import Body, FastAPI, HTTPException, Header, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError as exc:  # noqa: F841
    FASTAPI_AVAILABLE = False


from hippocampus import __version__, config
from hippocampus.mcp import tools
from hippocampus.storage import feedback as feedback_store

log = logging.getLogger("hippocampus.web")

CSRF_TOKEN = secrets.token_urlsafe(32)
MAX_REQUEST_BYTES = 1_000_000


# ---------------------------------------------------------------------------
# Pydantic request models (module-scope so FastAPI's type-adapter resolves them)
# ---------------------------------------------------------------------------

if FASTAPI_AVAILABLE:
    class RememberBody(BaseModel):
        content: str = Field(min_length=1, max_length=100_000)
        summary: str | None = Field(default=None, max_length=500)
        tags: list[str] = Field(default_factory=list, max_length=50)
        source_type: str = Field(default="web", min_length=1, max_length=100)
        source_ref: str | None = Field(default=None, max_length=2_000)
        pinned: bool = False

    class MarkBody(BaseModel):
        useful: bool
        reason: str | None = Field(default=None, max_length=500)

    class ForgetBody(BaseModel):
        reason: str | None = Field(default=None, max_length=1_000)

    class RecallBody(BaseModel):
        query: str = Field(min_length=1, max_length=10_000)
        limit: int = Field(default=5, ge=1, le=50)
        min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
        context_tag: str | None = Field(default=None, max_length=100)

    class ProgressLogBody(BaseModel):
        kind: Literal["ask", "done", "decision", "blocker", "next", "goal", "note"]
        content: str = Field(min_length=1, max_length=10_000)
        details: str | None = Field(default=None, max_length=100_000)
        client: str | None = Field(default=None, max_length=100)

    class ProgressEndBody(BaseModel):
        client: str | None = Field(default=None, max_length=100)
        distill: bool = False
        summary: str | None = Field(default=None, max_length=10_000)
        tags: list[str] = Field(default_factory=list, max_length=50)

    class ProgressUndoBody(BaseModel):
        client: str | None = Field(default=None, max_length=100)

    class ReindexBody(BaseModel):
        force: bool = False
        batch: int = Field(default=64, ge=1, le=1_000)

    class ConfigBody(BaseModel):
        key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
        value: Any


def _require_token(request: "Request", x_hippo_token: str | None) -> None:
    """Reject unauthenticated mutations. GETs are open; POSTs/DELETEs need the token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # Allow /api/csrf so the UI can fetch the token on first load.
    if request.url.path == "/api/csrf":
        return
    if x_hippo_token != CSRF_TOKEN:
        raise HTTPException(status_code=403, detail="invalid X-Hippo-Token")


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        from ipaddress import ip_address

        return ip_address(host).is_loopback
    except ValueError:
        return False


def create_app() -> "FastAPI":
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. "
            "Run `uv pip install -e '.[web]'` to enable the web UI."
        )

    app = FastAPI(title="Hippocampus", version=__version__)

    static_dir = Path(__file__).parent / "static"

    @app.middleware("http")
    async def auth_and_log(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid content-length"})
        try:
            _require_token(request, request.headers.get("X-Hippo-Token"))
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        log.info("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    # ------------------------------------------------------------------
    # CSRF / meta
    # ------------------------------------------------------------------

    @app.get("/api/csrf")
    def csrf() -> dict:
        return {"token": CSRF_TOKEN}

    # ------------------------------------------------------------------
    # Stats / top
    # ------------------------------------------------------------------

    @app.get("/api/stats")
    def api_stats() -> dict:
        return tools.get_stats()

    @app.get("/api/health")
    def api_health(duplicates: bool = False) -> dict:
        from hippocampus import maintenance

        return maintenance.health_snapshot(include_duplicates=duplicates)

    @app.get("/api/top")
    def api_top(limit: int | None = Query(default=None, ge=1, le=100)) -> dict:
        return tools.top_fragments(limit=limit)

    # ------------------------------------------------------------------
    # Fragments
    # ------------------------------------------------------------------

    @app.get("/api/fragments")
    def api_fragments(
        tag: str | None = Query(default=None, max_length=100),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict:
        return tools.list_fragments(tag=tag, min_confidence=min_confidence, limit=limit)

    @app.get("/api/fragments/{fragment_id}")
    def api_fragment_get(fragment_id: str) -> dict:
        out = tools.get_fragment(fragment_id, boost_on_read=False)
        if not out.get("found"):
            raise HTTPException(status_code=404, detail="fragment not found")
        return out

    @app.post("/api/fragments")
    def api_fragment_create(body: RememberBody = Body(...)) -> dict:
        return tools.remember(
            content=body.content,
            summary=body.summary,
            tags=body.tags,
            source_type=body.source_type,
            source_ref=body.source_ref,
            pinned=body.pinned,
        )

    @app.post("/api/fragments/{fragment_id}/pin")
    def api_pin(fragment_id: str) -> dict:
        return tools.pin(fragment_id)

    @app.post("/api/fragments/{fragment_id}/unpin")
    def api_unpin(fragment_id: str) -> dict:
        return tools.unpin(fragment_id)

    @app.post("/api/fragments/{fragment_id}/mark")
    def api_mark(fragment_id: str, body: MarkBody = Body(...)) -> dict:
        return tools.mark(fragment_id, useful=body.useful, reason=body.reason)

    @app.get("/api/triage")
    def api_triage(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        from hippocampus.storage.db import get_ro_conn

        with get_ro_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, summary, confidence, accessed, pinned, source_type,
                       length(content) AS size, created_at, last_accessed_at
                FROM fragments ORDER BY accessed DESC, confidence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"count": len(rows), "fragments": [dict(r) for r in rows]}

    @app.post("/api/fragments/{fragment_id}/forget")
    def api_forget(fragment_id: str, body: ForgetBody = Body(default_factory=ForgetBody)) -> dict:
        return tools.forget(fragment_id, reason=body.reason)

    @app.delete("/api/fragments/{fragment_id}")
    def api_fragment_delete(fragment_id: str) -> dict:
        from hippocampus.storage import fragments as F

        removed = F.delete(fragment_id)
        return {"deleted": removed, "fragment_id": fragment_id}

    # ------------------------------------------------------------------
    # Recall (hybrid)
    # ------------------------------------------------------------------

    @app.post("/api/recall")
    def api_recall(body: RecallBody = Body(...)) -> dict:
        return tools.recall(
            query=body.query,
            limit=body.limit,
            min_confidence=body.min_confidence,
            context_tag=body.context_tag,
        )

    # ------------------------------------------------------------------
    # Working memory
    # ------------------------------------------------------------------

    @app.get("/api/progress")
    def api_progress(
        client: str | None = Query(default=None, max_length=100),
        full: bool = False,
    ) -> dict:
        return tools.get_progress(client=client, full=full)

    @app.post("/api/progress")
    def api_progress_log(body: ProgressLogBody = Body(...)) -> dict:
        return tools.log_progress(
            kind=body.kind,
            content=body.content,
            details=body.details,
            client=body.client,
        )

    @app.post("/api/progress/end")
    def api_progress_end(body: ProgressEndBody = Body(default_factory=ProgressEndBody)) -> dict:
        return tools.end_progress(
            distill_to_fragment=body.distill,
            summary=body.summary,
            tags=body.tags,
            client=body.client,
        )

    @app.post("/api/progress/undo")
    def api_progress_undo(body: ProgressUndoBody = Body(default_factory=ProgressUndoBody)) -> dict:
        return tools.undo_last_entry(client=body.client)

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    @app.get("/api/embeddings/stats")
    def api_embed_stats() -> dict:
        from hippocampus.embeddings import search as semantic_search

        return semantic_search.stats()

    @app.post("/api/embeddings/reindex")
    def api_embed_reindex(body: ReindexBody = Body(default_factory=ReindexBody)) -> dict:
        from hippocampus.embeddings import search as semantic_search

        return semantic_search.reindex(force=body.force, batch=body.batch)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @app.get("/api/config")
    def api_config_get() -> dict:
        return {
            "path": str(config.config_path()),
            "settings": config.all_settings(),
        }

    @app.post("/api/config")
    def api_config_set(body: ConfigBody = Body(...)) -> dict:
        if body.key not in config._DEFAULTS:
            raise HTTPException(status_code=422, detail="unknown setting")
        config.set_setting(body.key, body.value)
        return api_config_get()

    # ------------------------------------------------------------------
    # Feedback log + associations
    # ------------------------------------------------------------------

    @app.get("/api/feedback")
    def api_feedback(limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return {"events": feedback_store.recent(limit=limit)}

    @app.get("/api/graph")
    def api_graph(
        min_weight: float = Query(default=5.0, ge=0.0),
        tag: str | None = Query(default=None, max_length=100),
        source_type: str | None = Query(default=None, max_length=100),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        pinned_only: bool = False,
        created_after: date | None = None,
        created_before: date | None = None,
    ) -> dict:
        from hippocampus.storage import associations

        return associations.get_graph(
            min_weight=min_weight,
            tag=tag,
            source_type=source_type,
            min_confidence=min_confidence,
            pinned_only=pinned_only,
            created_after=created_after.isoformat() if created_after else None,
            created_before=created_before.isoformat() if created_before else None,
        )

    @app.get("/api/associations/{fragment_id}")
    def api_associations(fragment_id: str) -> dict:
        from hippocampus.storage import associations

        rows = associations.get_associated(fragment_id, limit=50)
        return {
            "fragment_id": fragment_id,
            "associations": [
                {"other": other, "weight": weight, "co_accessed_count": count}
                for other, weight, count in rows
            ],
        }

    # ------------------------------------------------------------------
    # Static SPA
    # ------------------------------------------------------------------

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/")
        def root() -> Response:
            index = static_dir / "index.html"
            return FileResponse(index)

    return app


def run(
    host: str = "127.0.0.1",
    port: int = 7878,
    open_browser: bool = True,
) -> None:
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Run `uv pip install -e '.[web]'`."
        )
    if not _is_loopback_host(host):
        raise ValueError("Hippocampus web UI only binds to loopback addresses")
    tools._ensure_bootstrapped()

    app = create_app()
    log.info("serving Hippocampus web UI on http://%s:%d  (token=%s...)", host, port, CSRF_TOKEN[:8])
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="info")
