"""Hard per-job budget with reservations before every paid call (blueprint §14).

A reservation is taken before an agent starts, so parallel reviews cannot together
overshoot the limit. A slice of the limit is reserved for verification work. The
agent can never raise the limit; unknown prices fall back to a conservative cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Budget
from .state import Store, new_id, now

VERIFICATION_PURPOSES = {"review", "verify", "acceptance", "integration", "gate"}


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class BudgetStatus:
    limit: float
    committed: float  # settled spend + open reservations
    spent: float
    unknown_cost_calls: int

    @property
    def remaining(self) -> float:
        return self.limit - self.committed


def status(store: Store, job_id: str) -> BudgetStatus:
    job = store.job(job_id)
    open_reserved = store.one(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM budget_reservations WHERE job_id = ? AND state = 'open'", (job_id,)
    )[0]
    spent = store.one("SELECT COALESCE(SUM(settled_usd), 0) FROM budget_reservations WHERE job_id = ? AND state = 'settled'", (job_id,))[0]
    unknown = store.one("SELECT COUNT(*) FROM usage WHERE job_id = ? AND cost_known = 0", (job_id,))[0]
    return BudgetStatus(job["budget_limit_usd"], open_reserved + spent, spent, unknown)


def reserve(store: Store, cfg: Budget, job_id: str, purpose: str, amount: float | None = None) -> str:
    """Reserve conservatively; build work cannot eat the verification reserve."""
    amount = cfg.max_cost_per_agent_call if amount is None else amount
    with store.tx() as conn:
        limit = conn.execute("SELECT budget_limit_usd FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0]
        committed = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN state = 'open' THEN amount_usd ELSE settled_usd END), 0)"
            " FROM budget_reservations WHERE job_id = ? AND state IN ('open', 'settled')",
            (job_id,),
        ).fetchone()[0]
        usable = limit if purpose in VERIFICATION_PURPOSES else limit * (1 - cfg.reserve_for_verification_fraction)
        if committed + amount > usable + 1e-9:
            store.event(conn, job_id, "budget.denied", {"purpose": purpose, "amount": amount, "committed": committed, "usable": usable})
            raise BudgetExhausted(
                f"reserving ${amount:.2f} for {purpose} would exceed ${usable:.2f} (committed ${committed:.2f}, limit ${limit:.2f})"
            )
        reservation_id = new_id("bud")
        conn.execute(
            "INSERT INTO budget_reservations VALUES (?, ?, ?, ?, 'open', NULL, ?)",
            (reservation_id, job_id, purpose, amount, now()),
        )
        if committed + amount >= limit * cfg.warning_fraction:
            store.event(conn, job_id, "budget.warning", {"committed": committed + amount, "limit": limit})
    return reservation_id


def settle(
    store: Store,
    job_id: str,
    reservation_id: str,
    *,
    cost_usd: float | None,
    model_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    attempt_id: str | None,
    source: str,
) -> None:
    """Record actual usage. Unknown cost keeps the full reservation as spend (conservative)."""
    with store.tx() as conn:
        row = conn.execute("SELECT amount_usd, state FROM budget_reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
        if row is None or row["state"] != "open":
            return
        known = cost_usd is not None
        charged = cost_usd if known else row["amount_usd"]
        conn.execute(
            "UPDATE budget_reservations SET state = 'settled', settled_usd = ? WHERE reservation_id = ?", (charged, reservation_id)
        )
        conn.execute(
            "INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("use"), job_id, attempt_id, reservation_id, model_id, input_tokens, output_tokens, cost_usd, int(known), source, now()),
        )
        if known and cost_usd > row["amount_usd"]:
            store.event(conn, job_id, "budget.overrun", {"reserved": row["amount_usd"], "actual": cost_usd})


def release(store: Store, reservation_id: str) -> None:
    with store.tx() as conn:
        conn.execute("UPDATE budget_reservations SET state = 'released' WHERE reservation_id = ? AND state = 'open'", (reservation_id,))


def raise_budget(store: Store, job_id: str, new_limit: float, reason: str, actor: str = "operator") -> None:
    if not reason.strip():
        raise ValueError("raising the budget requires a reason")
    with store.tx() as conn:
        row = conn.execute("SELECT version, budget_limit_usd FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if new_limit <= row["budget_limit_usd"]:
            raise ValueError("the new budget must be higher than the current one")
        store.cas_update_job(conn, job_id, row["version"], budget_limit_usd=new_limit)
        store.event(conn, job_id, "budget.raised", {"from": row["budget_limit_usd"], "to": new_limit, "reason": reason, "actor": actor})
