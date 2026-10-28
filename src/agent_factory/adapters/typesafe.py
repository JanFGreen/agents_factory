"""Jev (TypeSafe AI) decision model: typed choices with confidence, for decisions with known options.

API: ``POST /v1/systemone`` with ``state`` and ``questions`` (type ``choice`` with ``criteria``);
the answer carries ``choice``, ``confidence`` and ``probabilities``. Any failure returns None so the
controller falls back to its own rules: the decision model is an optional second opinion, never a gate.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..config import DecisionModel
from ..difficulty import Choice, Decider

PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000  # output tokens are free (docs.typesafe.ai/models)


class JevDecider:
    name = "jev"

    def __init__(self, api_key: str, model: str, url: str, timeout: float):
        self._key = api_key
        self.model = model
        self.url = url
        self.timeout = timeout
        self.last_error: str | None = None

    def choose(self, state: dict, instructions: str, criteria: dict[str, str]) -> Choice | None:
        body = {"model": self.model, "state": state,
                "questions": {"decision": {"type": "choice", "instructions": instructions, "criteria": criteria}}}
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
            answer = data["answers"]["decision"]
            self.last_error = None
            return Choice(str(answer["choice"]), float(answer["confidence"]), int(data.get("usage", {}).get("input_tokens", 0)))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return None


def make_decider(cfg: DecisionModel) -> Decider | None:
    """The configured decision model, or None when disabled or its key is not set (the step is skipped)."""
    if cfg.provider != "jev":
        return None
    key = os.environ.get(cfg.api_key_env)
    if not key:
        return None
    return JevDecider(key, cfg.model, cfg.url, cfg.timeout_seconds)
