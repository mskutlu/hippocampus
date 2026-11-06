"""Hub endpoints: one dispatcher shared by the FastAPI routes and the local transport."""

# No `from __future__ import annotations`: FastAPI resolves the endpoint annotations at runtime.
from pathlib import Path
from typing import Any, Callable

from hippocampus.a2a import store
from hippocampus.a2a.store import A2AError


def _int(value: Any, name: str, default: int) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        raise A2AError(400, f"{name} must be an integer")


def _send(db: Path, q: dict, body: dict) -> dict:
    # body["hop"], if present, is ignored: the store computes it.
    return store.send(
        db, sender=str(body.get("from") or ""), to=str(body.get("to") or ""), content=str(body.get("content") or ""),
        msg_id=str(body.get("msg_id") or ""), thread_id=body.get("thread_id") or None, in_reply_to=body.get("in_reply_to") or None,
    )


def _inbox(db: Path, q: dict, body: dict) -> dict:
    return store.inbox(db, q.get("address", ""), _int(q.get("since"), "since", 0), _int(q.get("wait"), "wait", 0),
                       responder=str(q.get("responder") or "") == "1")


def _ack(db: Path, q: dict, body: dict) -> dict:
    ids = body.get("msg_ids")
    if not isinstance(ids, list):
        raise A2AError(400, "msg_ids required")
    return {"acked": store.ack(db, str(body.get("address") or ""), [str(i) for i in ids])}


def _pause(paused: bool) -> Callable[[Path, dict, dict], dict]:
    def handler(db: Path, q: dict, body: dict) -> dict:
        store.set_paused(db, paused)
        return {"paused": paused}
    return handler


HANDLERS: dict[tuple[str, str], Callable[[Path, dict, dict], dict]] = {
    ("POST", "/v1/a2a/send"): _send,
    ("GET", "/v1/a2a/inbox"): _inbox,
    ("POST", "/v1/a2a/ack"): _ack,
    ("GET", "/v1/a2a/agents"): lambda db, q, b: {"agents": store.agents(db)},
    ("POST", "/v1/a2a/pause"): _pause(True),
    ("POST", "/v1/a2a/resume"): _pause(False),
    ("GET", "/v1/a2a/audit"): lambda db, q, b: {"audit": store.audit(db, _int(q.get("limit"), "limit", 100))},
}


def dispatch(db: Path, method: str, route: str, query: dict | None = None, body: dict | None = None) -> dict:
    handler = HANDLERS.get((method, route))
    if handler is None:
        raise A2AError(404, f"no route {method} {route}")
    return handler(db, query or {}, body or {})


def mount(app, auth, db: Path) -> None:
    """Register the /v1/a2a/* routes. Called only when a2a is enabled."""
    from fastapi import Body, Depends, HTTPException, Request

    def make(method: str, route: str):
        def endpoint(request: Request, body: dict = Body(default=None)) -> dict:
            try:
                return dispatch(db, method, route, dict(request.query_params), body)
            except A2AError as exc:
                raise HTTPException(status_code=exc.status, detail=exc.reason)
        return endpoint

    for method, route in HANDLERS:
        app.add_api_route(route, make(method, route), methods=[method], dependencies=[Depends(auth)])
