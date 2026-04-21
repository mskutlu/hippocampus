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
from pathlib import Path
from typing import Any

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


from hippocampus import config
from hippocampus.mcp import tools
from hippocampus.storage import feedback as feedback_store

log = logging.getLogger("hippocampus.web")

CSRF_TOKEN = secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Pydantic request models (module-scope so FastAPI's type-adapter resolves them)
# ---------------------------------------------------------------------------

if FASTAPI_AVAILABLE:
    class RememberBody(BaseModel):
        content: str
        summary: str | None = None
        tags: list[str] = Field(default_factory=list)
        source_type: str = "web"
        source_ref: str | None = None
        pinned: bool = False

    class ForgetBody(BaseModel):
        reason: str | None = None

    class RecallBody(BaseModel):
        query: str
        limit: int = 5
        min_confidence: float = 0.0
        context_tag: str | None = None

    class ProgressLogBody(BaseModel):
        kind: str
        content: str
        details: str | None = None
        client: str | None = None

    class ProgressEndBody(BaseModel):
        client: str | None = None
        distill: bool = False
        summary: str | None = None
        tags: list[str] = Field(default_factory=list)

    class ProgressUndoBody(BaseModel):
        client: str | None = None

    class ReindexBody(BaseModel):
        force: bool = False
        batch: int = 64

    class ConfigBody(BaseModel):
        key: str
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


def create_app() -> "FastAPI":
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. "
            "Run `uv pip install -e '.[web]'` to enable the web UI."
        )

    app = FastAPI(title="Hippocampus", version=config._DEFAULTS.get("version", "1.2.0"))  # type: ignore[attr-defined]

    static_dir = Path(__file__).parent / "static"

    @app.middleware("http")
    async def auth_and_log(request: Request, call_next):
        try:
            _require_token(request, request.headers.get("X-Hippo-Token"))
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        response = await call_next(request)
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

    @app.get("/api/top")
    def api_top(limit: int | None = None) -> dict:
        return tools.top_fragments(limit=limit)

    # ------------------------------------------------------------------
    # Fragments
    # ------------------------------------------------------------------

    @app.get("/api/fragments")
    def api_fragments(
        tag: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
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
    def api_progress(client: str | None = None, full: bool = False) -> dict:
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
        config.set_setting(body.key.replace("-", "_").lower(), body.value)
        return api_config_get()

    # ------------------------------------------------------------------
    # Feedback log + associations
    # ------------------------------------------------------------------

    @app.get("/api/feedback")
    def api_feedback(limit: int = 50) -> dict:
        return {"events": feedback_store.recent(limit=limit)}

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
    # Make sure DB, mirror hooks, and dirs are ready before we take traffic.
    tools._ensure_bootstrapped()

    app = create_app()
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding to %s — anyone on your network can reach this. Token-guard is defence-in-depth only.", host)
    log.info("serving Hippocampus web UI on http://%s:%d  (token=%s...)", host, port, CSRF_TOKEN[:8])
    if open_browser and host in ("127.0.0.1", "localhost"):
        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="info")
