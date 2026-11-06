"""One mailbox client over two transports: the local store or HTTP to the hub."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode

from ulid import ULID

from hippocampus import config
from hippocampus.a2a import hub, store
from hippocampus.a2a.store import A2AError
from hippocampus.storage import sessions
from hippocampus.sync import client as sync_client

Transport = Callable[[str, str, dict | None], dict]


def local_transport(db: Path | None = None) -> Transport:
    path = db or store.db_path()

    def call(method: str, route: str, body: dict | None) -> dict:
        route, _, qs = route.partition("?")
        return hub.dispatch(path, method, route, dict(parse_qsl(qs)), body)

    return call


def default_transport() -> Transport:
    url = (config.get_setting("sync_url") or "").strip()
    token = (config.get_setting("sync_token") or "").strip()
    if url and token:
        return sync_client.http_transport(url, token, timeout=35.0)  # > the 25s max long-poll
    return local_transport()


def my_address(client: str) -> str:
    return f"{client}@{sync_client.device_id()}/{sessions.derive_session_key(client=client)}"


def resolve_to(to: str) -> str:
    return to if "@" in to else f"{to}@{sync_client.device_id()}"


class Mailbox:
    def __init__(self, transport: Transport | None = None):
        self._t = transport or default_transport()

    def _call(self, method: str, route: str, body: dict | None = None) -> dict:
        try:
            return self._t(method, route, body)
        except sync_client.SyncError as exc:
            raise A2AError(502, str(exc)) from exc

    def send(self, sender: str, to: str, content: str, thread_id: str | None = None, in_reply_to: str | None = None) -> dict:
        return self._call("POST", "/v1/a2a/send", {
            "from": sender, "to": to, "content": content, "thread_id": thread_id,
            "in_reply_to": in_reply_to, "msg_id": str(ULID()),
        })

    def inbox(self, address: str, since: int = 0, wait: int = 0, responder: bool = False) -> dict:
        q = {"address": address, "since": since, "wait": wait, **({"responder": 1} if responder else {})}  # old hubs ignore it
        return self._call("GET", "/v1/a2a/inbox?" + urlencode(q))

    def ack(self, address: str, msg_ids: list[str]) -> dict:
        return self._call("POST", "/v1/a2a/ack", {"address": address, "msg_ids": msg_ids})

    def agents(self) -> dict:
        return self._call("GET", "/v1/a2a/agents")
