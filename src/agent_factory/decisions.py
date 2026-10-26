"""Option scoring and exactly-one selection (blueprint §7.4).

The AI rates options; the controller computes ``score = Σ(weight × rating / 5)``,
applies the deterministic tie-break and rejects any decision that does not pick the
single best feasible option.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import SelectionConfig
from .schemas import Option, OptionsPayload


class DecisionError(ValueError):
    pass


@dataclass
class Selection:
    selected_option_id: str | None
    scores: dict[str, float]
    rejected: dict[str, list[str]]
    near_tie: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "selected_option_id": self.selected_option_id,
            "scores": self.scores,
            "rejected": self.rejected,
            "near_tie": self.near_tie,
            "notes": self.notes,
        }


def score(option: Option, cfg: SelectionConfig) -> float:
    assert option.ratings is not None
    weights = cfg.weights.model_dump()
    ratings = option.ratings.model_dump()
    return round(sum(weights[name] * ratings[name] / cfg.ratings.max for name in weights), 6)


def _tie_key(option: Option, cfg: SelectionConfig, value: float) -> tuple:
    key: list = [-value]
    ratings = option.ratings
    assert ratings is not None
    for rule in cfg.tie_break:
        if rule == "correctness_desc":
            key.append(-ratings.correctness)
        elif rule == "maintainability_desc":
            key.append(-ratings.maintainability)
        elif rule == "estimated_cost_asc":
            key.append(option.estimated_cost_usd if option.estimated_cost_usd is not None else float("inf"))
        elif rule == "estimated_time_asc":
            key.append(option.estimated_minutes if option.estimated_minutes is not None else float("inf"))
        elif rule == "option_id_asc":
            key.append(option.option_id)
    return tuple(key)


def select(payload: OptionsPayload, cfg: SelectionConfig, *, minimum_options: int) -> Selection:
    ids = [o.option_id for o in payload.options]
    if len(set(ids)) != len(ids):
        raise DecisionError(f"duplicate option ids: {ids}")
    if len(payload.options) < minimum_options:
        raise DecisionError(f"{len(payload.options)} options given, path requires at least {minimum_options}")
    rejected = {o.option_id: o.infeasible_reasons or ["marked infeasible"] for o in payload.options if not o.feasible}
    feasible = [o for o in payload.options if o.feasible]
    for option in feasible:
        if option.ratings is None:
            raise DecisionError(f"feasible option {option.option_id} has no ratings")
        if option.infeasible_reasons:
            raise DecisionError(f"option {option.option_id} is feasible but lists infeasible reasons")
    if not feasible:
        return Selection(None, {}, rejected, notes=["no feasible option"])
    scores = {o.option_id: score(o, cfg) for o in feasible}
    ranked = sorted(feasible, key=lambda o: _tie_key(o, cfg, scores[o.option_id]))
    best = ranked[0]
    near_tie = len(ranked) > 1 and abs(scores[best.option_id] - scores[ranked[1].option_id]) < cfg.near_tie_delta
    notes = []
    if near_tie:
        notes.append(f"near tie between {best.option_id} and {ranked[1].option_id}; tie-break rules applied")
    return Selection(best.option_id, scores, rejected, near_tie, notes)


def verify_ai_choice(payload: OptionsPayload, selection: Selection) -> None:
    """Reject an AI decision that names a lower-scoring or non-existent option (AF-05)."""
    if payload.selected_option_id is None:
        return
    if payload.selected_option_id != selection.selected_option_id:
        raise DecisionError(
            f"AI selected {payload.selected_option_id!r} but the controller's best feasible option is "
            f"{selection.selected_option_id!r} (scores {selection.scores})"
        )
