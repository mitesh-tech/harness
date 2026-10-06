"""SemIf over HTTP: the Jev-style API `POST /v1/systemone` served by semif-serve."""

import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from harness.semif.client import Choice, DecisionModelError, Question

START_HINT = (
    "start it with: ~/.harness/semif/.venv/bin/semif-serve --model Qwen/Qwen3.5-4B "
    "--revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a --backend mlx --port 8010"
)


class SemIfHttp:
    def __init__(self, url: str, timeout: float = 30, client: httpx.Client | None = None):
        self.url = url
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    # ── the interface ───────────────────────────────────────────────

    def choose(self, task: str, question: Question) -> Choice:
        body = self.choice_request(task, question)
        answer = self._post(body, question.id)
        try:
            return Choice(
                answer=answer["choice"],
                confidence=float(answer["confidence"]),
                probabilities={k: float(v) for k, v in answer["probabilities"].items()},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionModelError(f"unexpected answer from SemIf: {answer!r}") from exc

    def yes_no(self, task: str, question_id: str, instructions: str) -> float:
        body = {
            "state": task,
            "model": "semif",
            "questions": {question_id: {"type": "noul", "instructions": instructions}},
        }
        answer = self._post(body, question_id)
        try:
            return float(answer["noul"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionModelError(f"unexpected answer from SemIf: {answer!r}") from exc

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def choice_request(task: str, question: Question) -> dict:
        return {
            "state": task,
            "model": "semif",  # required by the API; the server ignores its value
            "questions": {
                question.id: {
                    "type": "choice",
                    "instructions": question.instructions,
                    "criteria": {o.name: o.text() for o in question.options},
                }
            },
        }

    def health(self) -> tuple[dict, float]:
        """Server status and model details, plus the time one test question took."""
        parts = urlsplit(self.url)
        health_url = urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
        try:
            status = self._client.get(health_url).json()
        except httpx.HTTPError as exc:
            raise DecisionModelError(f"SemIf is not reachable at {self.url}; {START_HINT}") from exc
        started = time.perf_counter()
        self.yes_no("Hello there.", "check", "Is this a greeting?")
        return status, time.perf_counter() - started

    def _post(self, body: dict, question_id: str) -> dict:
        try:
            response = self._client.post(self.url, json=body)
        except httpx.TimeoutException as exc:
            raise DecisionModelError(
                f"SemIf took longer than {self.timeout:g}s to answer '{question_id}'"
            ) from exc
        except httpx.HTTPError as exc:
            raise DecisionModelError(f"SemIf is not reachable at {self.url}; {START_HINT}") from exc
        if response.status_code != 200:
            raise DecisionModelError(
                f"SemIf answered {response.status_code} for '{question_id}': {response.text[:300]}"
            )
        try:
            return response.json()["answers"][question_id]
        except (ValueError, KeyError) as exc:
            raise DecisionModelError(f"unexpected reply from SemIf: {response.text[:300]}") from exc
