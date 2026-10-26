"""The shared analysis-round counter (blueprint §7.4, §7.7).

One counter per feature covers options, spec evaluation and decomposition review.
It does not reset on restart, model switch, replan or child tasks. Only an operator
can raise the limit, with a reason, and history is kept.
"""

from __future__ import annotations

from dataclasses import dataclass

from .identities import digest
from .state import Store, new_id, now


class AnalysisLimitReached(RuntimeError):
    pass


@dataclass
class RoundState:
    used: int
    limit: int
    current_number: int | None
    current_round_id: str | None

    @property
    def remaining(self) -> int:
        return self.limit - self.used


def round_state(store: Store, job_id: str) -> RoundState:
    job = store.job(job_id)
    rows = store.all("SELECT * FROM analysis_rounds WHERE job_id = ? ORDER BY number", (job_id,))
    open_rows = [r for r in rows if r["ended_at"] is None]
    current = open_rows[-1] if open_rows else None
    return RoundState(
        used=len(rows),
        limit=job["analysis_rounds_limit"],
        current_number=current["number"] if current else None,
        current_round_id=current["round_id"] if current else None,
    )


def start_round(store: Store, job_id: str) -> RoundState:
    """Open the next round, or raise when the shared limit is exhausted."""
    with store.tx() as conn:
        job = conn.execute("SELECT analysis_rounds_limit FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        rows = conn.execute("SELECT number, ended_at FROM analysis_rounds WHERE job_id = ? ORDER BY number", (job_id,)).fetchall()
        if rows and rows[-1]["ended_at"] is None:
            number = rows[-1]["number"]  # idempotent on resume
        else:
            if len(rows) >= job["analysis_rounds_limit"]:
                raise AnalysisLimitReached(f"{len(rows)} of {job['analysis_rounds_limit']} analysis rounds used")
            number = len(rows) + 1
            conn.execute(
                "INSERT INTO analysis_rounds (round_id, job_id, number, started_at) VALUES (?, ?, ?, ?)",
                (new_id("round"), job_id, number, now()),
            )
            store.event(conn, job_id, "analysis.round_started", {"number": number})
    return round_state(store, job_id)


def end_round(store: Store, job_id: str, outcome: str, open_findings: list[str]) -> bool:
    """Close the current round. Returns whether it made progress versus the previous one."""
    with store.tx() as conn:
        rows = conn.execute("SELECT * FROM analysis_rounds WHERE job_id = ? ORDER BY number", (job_id,)).fetchall()
        if not rows or rows[-1]["ended_at"] is not None:
            return True
        current = rows[-1]
        findings_digest = digest(sorted(open_findings))
        previous = rows[-2] if len(rows) > 1 else None
        progress = outcome == "PASS" or previous is None or previous["open_findings_digest"] != findings_digest
        conn.execute(
            "UPDATE analysis_rounds SET ended_at = ?, outcome = ?, open_findings_digest = ?, progress = ? WHERE round_id = ?",
            (now(), outcome, findings_digest, int(progress), current["round_id"]),
        )
        store.event(conn, job_id, "analysis.round_ended", {"number": current["number"], "outcome": outcome, "progress": progress})
    return progress


def consecutive_rounds_without_progress(store: Store, job_id: str) -> int:
    rows = store.all("SELECT progress FROM analysis_rounds WHERE job_id = ? AND ended_at IS NOT NULL ORDER BY number DESC", (job_id,))
    count = 0
    for row in rows:
        if row["progress"]:
            break
        count += 1
    return count


def raise_limit(store: Store, job_id: str, new_limit: int, reason: str, actor: str = "operator") -> None:
    if not reason.strip():
        raise ValueError("raising the analysis limit requires a reason")
    with store.tx() as conn:
        row = conn.execute("SELECT version, analysis_rounds_limit FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if new_limit <= row["analysis_rounds_limit"]:
            raise ValueError("the new limit must be higher than the current one")
        store.cas_update_job(conn, job_id, row["version"], analysis_rounds_limit=new_limit)
        store.event(conn, job_id, "analysis.limit_raised", {"from": row["analysis_rounds_limit"], "to": new_limit, "reason": reason, "actor": actor})
