"""Transactional outbox for external side effects (blueprint §12.2).

Intent and state change are written in one transaction; a separate executor performs
the operation. Delivery is at-least-once with deduplication and reconciliation: after
a timeout the state is UNKNOWN and the effect is looked up before any retry.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from .state import Store, new_id, now


class OperationTimeout(RuntimeError):
    """The remote may or may not have applied the operation."""


class OperationFailed(RuntimeError):
    pass


class EffectHandler(Protocol):
    def perform(self, kind: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...

    def lookup(self, kind: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any] | None:
        """Return the already-applied effect, or None if it did not happen."""
        ...


def idempotency_key(repo_id: str, job_id: str, revision: str, operation: str) -> str:
    return f"{repo_id}:{job_id}:{revision}:{operation}"


def enqueue(store: Store, conn: sqlite3.Connection, job_id: str | None, kind: str, payload: dict[str, Any], key: str) -> str:
    """Must be called inside the same transaction as the state change it belongs to."""
    existing = conn.execute("SELECT op_id FROM outbox WHERE idempotency_key = ?", (key,)).fetchone()
    if existing:
        return existing["op_id"]
    op_id = new_id("op")
    conn.execute(
        "INSERT INTO outbox VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, ?, ?)",
        (op_id, job_id, kind, key, json.dumps(payload), now(), now()),
    )
    store.event(conn, job_id, "outbox.enqueued", {"op_id": op_id, "kind": kind, "key": key})
    return op_id


@dataclass
class OpResult:
    op_id: str
    state: str
    result: dict[str, Any] | None
    error: str | None = None


def _set(store: Store, op_id: str, state: str, *, result: dict | None = None, error: str | None = None, bump: bool = False) -> None:
    with store.tx() as conn:
        conn.execute(
            "UPDATE outbox SET state = ?, result_json = COALESCE(?, result_json), last_error = ?, updated_at = ?,"
            " attempts = attempts + ? WHERE op_id = ?",
            (state, json.dumps(result) if result is not None else None, error, now(), int(bump), op_id),
        )
        row = conn.execute("SELECT job_id, kind FROM outbox WHERE op_id = ?", (op_id,)).fetchone()
        store.event(conn, row["job_id"], f"outbox.{state}", {"op_id": op_id, "kind": row["kind"], "error": error})


def execute(store: Store, op_id: str, handler: EffectHandler, max_attempts: int = 3) -> OpResult:
    row = store.one("SELECT * FROM outbox WHERE op_id = ?", (op_id,))
    if row is None:
        raise KeyError(op_id)
    payload = json.loads(row["payload_json"])
    if row["state"] == "confirmed":
        return OpResult(op_id, "confirmed", json.loads(row["result_json"]))
    if row["state"] in ("inflight", "unknown"):
        # A previous executor may have applied it (crash or timeout): reconcile first.
        found = handler.lookup(row["kind"], payload, row["idempotency_key"])
        if found is not None:
            _set(store, op_id, "confirmed", result=found)
            return OpResult(op_id, "confirmed", found)
    if row["attempts"] >= max_attempts:
        _set(store, op_id, "failed", error="max attempts reached")
        return OpResult(op_id, "failed", None, "max attempts reached")
    _set(store, op_id, "inflight", bump=True)
    try:
        result = handler.perform(row["kind"], payload, row["idempotency_key"])
    except OperationTimeout as exc:
        _set(store, op_id, "unknown", error=str(exc))
        return OpResult(op_id, "unknown", None, str(exc))
    except OperationFailed as exc:
        _set(store, op_id, "failed", error=str(exc))
        return OpResult(op_id, "failed", None, str(exc))
    except PermissionError as exc:  # policy broker refusal is audited, never retried
        with store.tx() as conn:
            store.event(conn, row["job_id"], "broker.denied", {"op_id": op_id, "kind": row["kind"], "reason": str(exc)})
        _set(store, op_id, "failed", error=f"denied: {exc}")
        return OpResult(op_id, "failed", None, f"denied: {exc}")
    _set(store, op_id, "confirmed", result=result)
    return OpResult(op_id, "confirmed", result)


def pending_ops(store: Store, job_id: str | None = None) -> list[sqlite3.Row]:
    if job_id is None:
        return store.all("SELECT * FROM outbox WHERE state IN ('pending', 'inflight', 'unknown') ORDER BY created_at")
    return store.all(
        "SELECT * FROM outbox WHERE job_id = ? AND state IN ('pending', 'inflight', 'unknown') ORDER BY created_at", (job_id,)
    )
