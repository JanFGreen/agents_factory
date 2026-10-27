"""SQLite state repository: migrations, short transactions and CAS on job versions.

SQLite is the single source of truth for controller state. Transactions are short;
never hold one across an LLM call, a GitHub call, tests or dependency installs.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE features (
            feature_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            repo_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            original_request TEXT NOT NULL,
            goal TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            source_snapshot_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (feature_id, revision)
        );
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            feature_id TEXT NOT NULL,
            repo_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            scope_revision INTEGER NOT NULL,
            state TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            path TEXT,
            risk TEXT,
            repo_path TEXT NOT NULL,
            base_branch TEXT,
            base_sha TEXT,
            head_sha TEXT,
            worktree TEXT,
            branch TEXT,
            analysis_rounds_limit INTEGER NOT NULL,
            build_attempts INTEGER NOT NULL DEFAULT 0,
            integration_rebuilds INTEGER NOT NULL DEFAULT 0,
            spikes_used INTEGER NOT NULL DEFAULT 0,
            budget_limit_usd REAL NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}',
            blocked_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX jobs_one_active_per_scope
            ON jobs (repo_id, source_id, scope_revision) WHERE active = 1;
        CREATE TABLE tasks (
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            task_id TEXT NOT NULL,
            order_idx INTEGER NOT NULL,
            spec_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            plan_digest TEXT NOT NULL,
            accepted_head_sha TEXT,
            PRIMARY KEY (job_id, task_id)
        );
        CREATE TABLE attempts (
            attempt_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            task_id TEXT,
            role TEXT NOT NULL,
            state TEXT NOT NULL,
            lease_epoch INTEGER,
            runtime TEXT,
            runtime_run_id TEXT,
            session_id TEXT,
            model_id TEXT,
            pid INTEGER,
            error_fingerprint TEXT,
            result_id TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE TABLE leases (
            lease_key TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            expires_at REAL NOT NULL,
            pid INTEGER
        );
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            kind TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            from_version INTEGER,
            to_version INTEGER,
            payload_json TEXT NOT NULL DEFAULT '{}',
            at TEXT NOT NULL
        );
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            kind TEXT NOT NULL,
            job_id TEXT,
            rel_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            producer_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE results (
            result_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            task_id TEXT,
            kind TEXT NOT NULL,
            verdict TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            producer_json TEXT NOT NULL,
            payload_artifact_id TEXT NOT NULL,
            signature TEXT NOT NULL,
            analysis_round INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE findings (
            finding_key TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            task_id TEXT,
            result_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            blocking INTEGER NOT NULL,
            status TEXT NOT NULL,
            problem TEXT NOT NULL,
            expected_fix TEXT,
            location_json TEXT,
            requirement_ids_json TEXT NOT NULL DEFAULT '[]',
            author TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            head_sha TEXT,
            base_sha TEXT,
            criteria_digest TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT,
            valid INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE outbox (
            op_id TEXT PRIMARY KEY,
            job_id TEXT,
            kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE usage (
            usage_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            attempt_id TEXT,
            reservation_id TEXT,
            model_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            cost_known INTEGER NOT NULL,
            source TEXT NOT NULL,
            at TEXT NOT NULL
        );
        CREATE TABLE budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            state TEXT NOT NULL,
            settled_usd REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE resource_allocations (
            alloc_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            job_id TEXT,
            owner TEXT,
            created_at TEXT NOT NULL,
            released_at TEXT
        );
        CREATE UNIQUE INDEX resource_unique_active
            ON resource_allocations (kind, value) WHERE released_at IS NULL;
        CREATE TABLE complexity_assessments (
            assessment_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            feature_revision INTEGER NOT NULL,
            source TEXT NOT NULL,
            dimensions_json TEXT NOT NULL,
            score INTEGER,
            path TEXT NOT NULL,
            risk TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE analysis_rounds (
            round_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            outcome TEXT,
            open_findings_digest TEXT,
            progress INTEGER,
            UNIQUE (job_id, number)
        );
        """,
    ),
    (
        2,
        """
        ALTER TABLE attempts ADD COLUMN handle_json TEXT;
        CREATE INDEX events_job ON events (job_id, seq);
        CREATE INDEX results_job_kind ON results (job_id, kind, task_id)
        """,
    ),
]


class ConcurrentModification(RuntimeError):
    """CAS failed: somebody else changed the job since it was read."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
        # One connection is shared by the controller's threads (parallel task lanes): every use is serialized.
        self.lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    # --- transactions -----------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Short write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self.tx() as conn:
                for statement in sql.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now()))

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0

    # --- generic helpers ---------------------------------------------------------

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return list(self.conn.execute(sql, params).fetchall())

    def event(
        self,
        conn: sqlite3.Connection,
        job_id: str | None,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        from_version: int | None = None,
        to_version: int | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO events (job_id, kind, from_state, to_state, from_version, to_version, payload_json, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, kind, from_state, to_state, from_version, to_version, json.dumps(payload or {}), now()),
        )

    # --- jobs --------------------------------------------------------------------

    def job(self, job_id: str) -> sqlite3.Row:
        row = self.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            raise KeyError(f"unknown job {job_id}")
        return row

    def job_context(self, job_id: str) -> dict[str, Any]:
        return json.loads(self.job(job_id)["context_json"])

    def cas_update_job(
        self, conn: sqlite3.Connection, job_id: str, expected_version: int, **fields: Any
    ) -> int:
        fields["version"] = expected_version + 1
        fields["updated_at"] = now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        cur = conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ? AND version = ?",
            (*fields.values(), job_id, expected_version),
        )
        if cur.rowcount != 1:
            raise ConcurrentModification(f"job {job_id} changed since version {expected_version}")
        return expected_version + 1

    def update_job_context(self, job_id: str, **updates: Any) -> None:
        """Merge keys into the job's context blob under CAS."""
        with self.tx() as conn:
            row = conn.execute("SELECT version, context_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            ctx = json.loads(row["context_json"])
            ctx.update(updates)
            self.cas_update_job(conn, job_id, row["version"], context_json=json.dumps(ctx))

    def events_for(self, job_id: str) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM events WHERE job_id = ? ORDER BY seq", (job_id,))
