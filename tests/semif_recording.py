"""Record real SemIf responses once, replay them in tests.

Works at the HTTP level: the harness's real SemIf adapter builds every request
and parses every response; only the network call is answered from the recording.

    uv run pytest --record-semif   # ask the live server, save its answers
    uv run pytest                  # replay the saved answers (CI)
    uv run pytest --live-semif     # ask the live server, save nothing
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

RECORDING = Path(__file__).parent / "recordings" / "semif-bookstore.json"


def request_key(request: httpx.Request) -> str:
    body = json.loads(request.content) if request.content else None
    canonical = json.dumps([request.method, request.url.path, body], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def describe(request: httpx.Request) -> str:
    if not request.content:
        return f"{request.method} {request.url.path}"
    body = json.loads(request.content)
    questions = ", ".join(body.get("questions", {}))
    return f"task {body.get('state')!r}, question {questions}"


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, path: Path = RECORDING):
        data = json.loads(path.read_text()) if path.is_file() else {"exchanges": {}}
        self.exchanges = data["exchanges"]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        exchange = self.exchanges.get(request_key(request))
        if exchange is None:
            pytest.fail(
                f"No recorded SemIf answer for {describe(request)}. The question or its "
                "options changed since recording; re-record with `uv run pytest --record-semif` "
                "(needs the SemIf server running).",
                pytrace=False,
            )
        return httpx.Response(exchange["status"], json=exchange["response"])


class RecordingTransport(httpx.BaseTransport):
    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.exchanges: dict[str, dict] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        response.read()
        self.exchanges[request_key(request)] = {
            "request": json.loads(request.content) if request.content else request.url.path,
            "status": response.status_code,
            "response": response.json(),
        }
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    def save(self, path: Path = RECORDING) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = dict(sorted(self.exchanges.items()))
        path.write_text(json.dumps({"exchanges": ordered}, indent=2, ensure_ascii=False) + "\n")
