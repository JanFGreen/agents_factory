"""Content-addressed artifact store and signed verdict records.

A file hash does not prove who wrote a file. Only the controller holds the signing
key (outside any sandbox mount), so a result row is accepted only when its HMAC
verifies and its producer is a trusted component. Files exported into the job
branch (e.g. ``spec-evaluation.json``) are never read back as evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identities import InputIdentity, ResultKind, canonical_json, stale_fields
from .state import Store, new_id, now

TRUSTED_PRODUCERS = {"trusted-runner", "agent-adapter", "controller", "operator"}


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Producer:
    component: str  # trusted-runner | agent-adapter | controller | operator
    role: str
    runtime_run_id: str | None = None
    model_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "role": self.role,
            "runtime_run_id": self.runtime_run_id,
            "model_id": self.model_id,
        }


@dataclass(frozen=True)
class StoredResult:
    result_id: str
    kind: ResultKind
    verdict: str
    identity: InputIdentity
    producer: dict[str, Any]
    payload: dict[str, Any]
    task_id: str | None
    analysis_round: int | None


def _load_or_create_key(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


class EvidenceStore:
    def __init__(self, store: Store, data_dir: Path):
        self.store = store
        self.root = data_dir / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._key = _load_or_create_key(data_dir / "keys" / "evidence.key")

    # --- artifacts ---------------------------------------------------------------

    def put(self, content: bytes, kind: str, producer: Producer, job_id: str | None = None) -> str:
        sha = hashlib.sha256(content).hexdigest()
        rel = f"{sha[:2]}/{sha[2:4]}/{sha}"
        path = self.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{sha}.{uuid.uuid4().hex}.tmp")  # unique: parallel lanes may write the same content
            tmp.write_bytes(content)
            os.replace(tmp, path)
        artifact_id = new_id("art")
        with self.store.tx() as conn:
            conn.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, sha, kind, job_id, rel, len(content), json.dumps(producer.as_dict()), now()),
            )
        return artifact_id

    def put_json(self, value: Any, kind: str, producer: Producer, job_id: str | None = None) -> str:
        return self.put(canonical_json(value).encode("utf-8"), kind, producer, job_id)

    def read(self, artifact_id: str) -> bytes:
        row = self.store.one("SELECT sha256, rel_path FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        if row is None:
            raise EvidenceError(f"unknown artifact {artifact_id}")
        content = (self.root / row["rel_path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise EvidenceError(f"artifact {artifact_id} content does not match its hash")
        return content

    def read_json(self, artifact_id: str) -> Any:
        return json.loads(self.read(artifact_id))

    # --- signed results -----------------------------------------------------------

    def _sign(self, body: dict[str, Any]) -> str:
        return hmac.new(self._key, canonical_json(body).encode("utf-8"), hashlib.sha256).hexdigest()

    def record_result(
        self,
        *,
        job_id: str,
        kind: ResultKind,
        verdict: str,
        identity: InputIdentity,
        producer: Producer,
        payload: dict[str, Any],
        task_id: str | None = None,
        analysis_round: int | None = None,
        findings: list[dict[str, Any]] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        if producer.component not in TRUSTED_PRODUCERS:
            raise EvidenceError(f"untrusted producer {producer.component!r}")
        problems = identity.validate_real()
        if problems:
            raise EvidenceError("; ".join(problems))
        payload_id = self.put_json(payload, f"result:{kind}", producer, job_id)
        result_id = new_id("res")
        body = {
            "result_id": result_id,
            "job_id": job_id,
            "task_id": task_id,
            "kind": str(kind),
            "verdict": verdict,
            "identity": identity.model_dump(),
            "producer": producer.as_dict(),
            "payload_artifact_id": payload_id,
            "payload_sha256": self._artifact_sha(payload_id),
        }
        signature = self._sign(body)

        def write(c: sqlite3.Connection) -> None:
            c.execute(
                "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id, job_id, task_id, str(kind), verdict, canonical_json(identity.model_dump()),
                    canonical_json(producer.as_dict()), payload_id, signature, analysis_round, now(),
                ),
            )
            # Closure is confirmed by the evaluator's new run, never by the builder.
            reported = [f["id"] for f in findings or []]
            c.execute(
                "UPDATE findings SET status = 'fixed', updated_at = ? WHERE job_id = ? AND kind = ?"
                " AND task_id IS ? AND status = 'open'"
                + (f" AND finding_id NOT IN ({','.join('?' * len(reported))})" if reported else ""),
                (now(), job_id, str(kind), task_id, *reported),
            )
            for finding in findings or []:
                c.execute(
                    "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(finding_key) DO UPDATE SET status = excluded.status, severity = excluded.severity,"
                    " blocking = excluded.blocking, problem = excluded.problem, expected_fix = excluded.expected_fix,"
                    " location_json = excluded.location_json, updated_at = excluded.updated_at, result_id = excluded.result_id",
                    (
                        f"{job_id}:{task_id or '-'}:{kind}:{finding['id']}", finding["id"], job_id, task_id, result_id, str(kind),
                        finding["severity"], int(finding["blocking"]), finding.get("status", "open"),
                        finding["problem"], finding.get("expected_fix"),
                        json.dumps(finding.get("location")), json.dumps(finding.get("requirement_ids", [])),
                        producer.role, now(), now(),
                    ),
                )
            self.store.event(c, job_id, "result.recorded", {"result_id": result_id, "kind": str(kind), "verdict": verdict})

        if conn is not None:
            write(conn)
        else:
            with self.store.tx() as c:
                write(c)
        return result_id

    def _artifact_sha(self, artifact_id: str) -> str | None:
        row = self.store.one("SELECT sha256 FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return row["sha256"] if row else None

    def _row_to_result(self, row: sqlite3.Row) -> StoredResult:
        body = {
            "result_id": row["result_id"],
            "job_id": row["job_id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "verdict": row["verdict"],
            "identity": json.loads(row["identity_json"]),
            "producer": json.loads(row["producer_json"]),
            "payload_artifact_id": row["payload_artifact_id"],
            "payload_sha256": self._artifact_sha(row["payload_artifact_id"]),
        }
        if not hmac.compare_digest(self._sign(body), row["signature"]):
            raise EvidenceError(f"result {row['result_id']} signature is invalid (forged or tampered)")
        if body["producer"]["component"] not in TRUSTED_PRODUCERS:
            raise EvidenceError(f"result {row['result_id']} has an untrusted producer")
        return StoredResult(
            result_id=row["result_id"],
            kind=ResultKind(row["kind"]),
            verdict=row["verdict"],
            identity=InputIdentity.model_validate(body["identity"]),
            producer=body["producer"],
            payload=self.read_json(row["payload_artifact_id"]),
            task_id=row["task_id"],
            analysis_round=row["analysis_round"],
        )

    def latest(self, job_id: str, kind: ResultKind, task_id: str | None = None, job_level: bool = False) -> StoredResult | None:
        """Latest result of ``kind``: of the task, of any task (default), or of the job itself (``job_level``)."""
        if job_level:
            row = self.store.one(
                "SELECT * FROM results WHERE job_id = ? AND kind = ? AND task_id IS NULL ORDER BY rowid DESC LIMIT 1",
                (job_id, str(kind)),
            )
        elif task_id is None:
            row = self.store.one(
                "SELECT * FROM results WHERE job_id = ? AND kind = ? ORDER BY rowid DESC LIMIT 1",
                (job_id, str(kind)),
            )
        else:
            row = self.store.one(
                "SELECT * FROM results WHERE job_id = ? AND kind = ? AND task_id = ? ORDER BY rowid DESC LIMIT 1",
                (job_id, str(kind), task_id),
            )
        return None if row is None else self._row_to_result(row)

    def current_pass(
        self, job_id: str, kind: ResultKind, current: InputIdentity, task_id: str | None = None, job_level: bool = False
    ) -> tuple[bool, str]:
        """True only for a verified, PASS, non-stale latest result of ``kind``."""
        try:
            result = self.latest(job_id, kind, task_id, job_level=job_level)
        except EvidenceError as exc:
            return False, str(exc)
        if result is None:
            return False, f"no {kind} result"
        if result.verdict != "PASS":
            return False, f"latest {kind} verdict is {result.verdict}"
        stale = stale_fields(kind, result.identity, current)
        if stale:
            return False, f"{kind} result is stale for {', '.join(stale)}"
        return True, "ok"

    def open_blocking_findings(self, job_id: str, task_id: str | None = None) -> list[sqlite3.Row]:
        if task_id is None:
            return self.store.all(
                "SELECT * FROM findings WHERE job_id = ? AND blocking = 1 AND status = 'open'", (job_id,)
            )
        return self.store.all(
            "SELECT * FROM findings WHERE job_id = ? AND task_id = ? AND blocking = 1 AND status = 'open'",
            (job_id, task_id),
        )
