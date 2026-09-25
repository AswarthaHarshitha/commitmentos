"""A scripted, OpenAI-compatible stand-in for the LLM, so workflow behaviour can be tested deterministically.

Queue what the model should answer; each call to /v1/chat/completions pops the next item (the last item repeats).
    dict            -> a normal reply whose content is that JSON
    Raw("text")     -> a normal reply with arbitrary text (e.g. malformed JSON)
    Fail(503)       -> an HTTP error
    Slow(2.0, item) -> wait, then behave like `item`
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Raw:
    text: str


@dataclass
class Fail:
    status: int
    body: dict[str, Any] | None = None


@dataclass
class Slow:
    seconds: float
    then: Any


class StubLLM:
    def __init__(self) -> None:
        self.script: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                item = outer._next(body)
                if isinstance(item, Slow):
                    time.sleep(item.seconds)
                    item = item.then
                if isinstance(item, Fail):
                    payload = json.dumps(item.body or {"error": {"message": f"stub failure {item.status}"}}).encode()
                    self.send_response(item.status)
                else:
                    content = item.text if isinstance(item, Raw) else json.dumps(item)
                    payload = json.dumps(
                        {
                            "id": "stub-1", "model": "stub",
                            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _next(self, request: dict[str, Any]) -> Any:
        with self._lock:
            self.calls.append(request)
            if not self.script:
                return Fail(500, {"error": {"message": "stub LLM has nothing scripted"}})
            return self.script.pop(0) if len(self.script) > 1 else self.script[0]

    def reset(self, *script: Any) -> None:
        with self._lock:
            self.script = list(script)
            self.calls = []

    def stop(self) -> None:
        self.server.shutdown()
