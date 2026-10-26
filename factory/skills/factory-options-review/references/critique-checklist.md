# Option critique checklist

Apply to every option in `options.md`. Each "no" is a finding with evidence.

## Reality

- [ ] The option describes a concrete approach in **this** repository (modules, files, existing patterns cited).
- [ ] It is materially different from the other options (data flow, boundary, or dependency choice), not a renamed duplicate.
- [ ] It is not a straw man: a competent engineer could reasonably choose it.

## Hard constraints (any violation ⇒ infeasible)

- [ ] Every feature constraint checked explicitly (e.g. "use existing login", "no other users' data").
- [ ] Policy minimums respected: security path rules, no new credentials or trust boundaries beyond policy, allowed paths only.
- [ ] Required permissions, credentials and environments are actually granted.

## Feasibility

- [ ] Dependencies exist (or adding them is allowed and lock changes are controlled).
- [ ] Migrations, if any, have a rollback path.
- [ ] Estimated cost and minutes fit the remaining feature budget.
- [ ] Unknowns are named; a decisive unknown has a proposed bounded spike (max 1 per feature).

## Ratings (0-5, higher is better)

- [ ] `goal_fit`: covers every intent of the original request; no hidden narrowing.
- [ ] `correctness`: defect/security risk argued from evidence, not optimism.
- [ ] `maintainability`: fits existing patterns; no parallel mechanism without reason.
- [ ] `delivery_time` / `cost`: derived from explicit `estimated_minutes` / `estimated_cost_usd`; cheaper/faster ⇒ higher rating.

## Scope

- [ ] The option builds `required_now` only; `committed_next` affects only contracts/constraints; `hypothetical` builds nothing.
- [ ] The option does not reach PASS by excluding a requirement.

## Common findings

| Smell | Example | Severity |
|---|---|---|
| Unchecked isolation | materialized view without owner filter | high, blocking |
| Unsupported rating | cost 5 with no estimate | medium, blocking |
| Straw man | "rewrite the ORM" as alternative to a query | medium, blocking |
| Speculative build | export endpoint added "for later" | medium, blocking |
| Style-only remark | naming of option headings | low, non-blocking |
