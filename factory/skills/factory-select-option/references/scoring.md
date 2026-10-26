# Option scoring rubric (policy default)

Source: blueprint §7.4. Weights live in the selection policy and must sum to 1.0;
the values below are the shipped defaults. Changing them is a policy change
(new `policy_digest`), never something a skill does.

| Criterion | Key | Weight | 5 means | 0 means |
|---|---|---|---|---|
| Goal fit | `goal_fit` | 0.30 | covers every intent of the original request | misses the core intent |
| Correctness / risk | `correctness` | 0.25 | well-understood, low defect and security risk | likely wrong or unsafe |
| Maintainability | `maintainability` | 0.20 | fits existing patterns, small surface | new parallel mechanism, hard to change |
| Delivery time | `delivery_time` | 0.15 | fastest credible estimate among options | far slowest |
| Cost | `cost` | 0.10 | cheapest credible estimate among options | far most expensive |

Higher rating is always better. For `delivery_time` and `cost` a lower estimate
gives a higher rating.

## Formula

```
score(option) = Σ weight[k] * rating[k] / 5      # range 0.0 – 1.0
```

Only options with `feasible: true` are candidates. Exactly one option is selected.

## Deterministic tie-break (applied in order when scores are equal)

1. higher `correctness` rating
2. higher `maintainability` rating
3. lower `estimated_cost_usd`
4. lower `estimated_minutes`
5. lexicographically smallest `option_id` (stable ID)

## Worked example

| Option | goal_fit | correctness | maintainability | delivery_time | cost | score |
|---|---|---|---|---|---|---|
| OPT-A | 5 | 4 | 4 | 3 | 3 | 0.30·1.0 + 0.25·0.8 + 0.20·0.8 + 0.15·0.6 + 0.10·0.6 = **0.81** |
| OPT-B | 4 | 4 | 3 | 5 | 5 | 0.24 + 0.20 + 0.12 + 0.15 + 0.10 = **0.81** |

Tie at 0.81 → step 1 equal (4 vs 4) → step 2: OPT-A maintainability 4 > 3 → **OPT-A**.

## Near-tie rule

If |score₁ − score₂| < 0.03 and a decisive fact is unknown, use the remaining
feature spike (max 1). Otherwise decide by the tie-break and state the
uncertainty explicitly. A score never substitutes for spec/decomposition PASS.
