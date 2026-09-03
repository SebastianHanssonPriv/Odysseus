"""Reusable Qlik Engine JSON-RPC (QIX) client over WebSocket.

This is the shared connection layer that future Qlik Engine API tools should
import instead of re-solving auth, the websocket handshake, and request/
response correlation each time — the Qlik-side counterpart to
powerbi_client.py in the Power BI tool. It only supports the synchronous,
one-request-at-a-time call pattern a CLI/batch tool needs: connect, make a
handful of calls against one app, close. It deliberately does not implement
Enigma.js-style live object subscriptions (onChanged callbacks) — no tool in
this suite currently needs a standing connection watching for engine-side
changes.
"""

from __future__ import annotations

import itertools
import json

from websockets.sync.client import connect as ws_connect

from auth import QlikTokenProvider


class EngineApiError(RuntimeError):
    """Raised when the Engine API returns a JSON-RPC error object."""


class QixEngineSession:
    """One open WebSocket session against a single Qlik Cloud app.

    Use as a context manager:
        with QixEngineSession(tokens, app_id) as session:
            script = session.call(session.doc_handle, "GetScript")
    """

    def __init__(self, token_provider: QlikTokenProvider, app_id: str):
        self._tokens = token_provider
        self._app_id = app_id
        self._ws = None
        self._id_counter = itertools.count(1)
        self.doc_handle: int | None = None

    def __enter__(self) -> "QixEngineSession":
        url = f"{self._to_wss(self._tokens.tenant_url)}/app/{self._app_id}"
        self._ws = ws_connect(
            url,
            additional_headers={"Authorization": f"Bearer {self._tokens.token()}"},
            open_timeout=60,
        )
        # The engine's first message on connect is an OnConnected session
        # notification (no "id"); drain it before issuing any call.
        self._read_message()
        # Connecting straight to /app/{id} auto-opens that app for the
        # session, so the doc handle is fetched via GetActiveDoc rather than
        # OpenDoc (OpenDoc is for a bare /app/engineData session that has not
        # named an app yet).
        result = self.call(-1, "GetActiveDoc")
        self.doc_handle = result["qReturn"]["qHandle"]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._ws is not None:
            self._ws.close()

    @staticmethod
    def _to_wss(tenant_url: str) -> str:
        return tenant_url.replace("https://", "wss://").replace("http://", "ws://")

    def call(self, handle: int, method: str, params: list | None = None) -> dict:
        request_id = next(self._id_counter)
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "handle": handle,
            "method": method,
            "params": params if params is not None else [],
        }
        self._ws.send(json.dumps(request))
        return self._await_response(request_id, method)

    def _await_response(self, request_id: int, method: str) -> dict:
        # Skip asynchronous change notifications (no "id") that can arrive
        # ahead of the matching response — this client makes single request/
        # response calls only and never subscribes to live object updates.
        while True:
            message = self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise EngineApiError(f"{method} failed: {message['error']}")
            return message["result"]

    def _read_message(self) -> dict:
        raw = self._ws.recv()
        return json.loads(raw)
