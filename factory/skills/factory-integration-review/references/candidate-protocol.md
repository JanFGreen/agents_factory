# Integration candidate protocol (blueprint §13.3, §5)

Owned by the controller; this skill reviews steps 2-3 and informs step 4.

1. Read and freeze `base_sha` / `head_sha`; build the candidate (`candidate_tree_sha`).
2. Resolve conflicts. A semantic change returns to spec/plan (OPTIONS_DRAFT).
3. Run required checks, acceptance and impact review **on this candidate**.
4. Generate the merge recommendation; verify the current approval/policy.
5. Confirm PR, repo, head and base identity; required checks complete, not `pending`.
6. Merge without `--admin`, without bypassing branch protection, without force.
7. Confirm the real merge on the server; record the final SHA and its relation to
   the tested tree (squash/rebase: do not compare SHAs naively).
8. Cleanup only after that; cleanup failure never turns MERGED into FAILED.

## Rules this review enforces

| Rule | Consequence |
|---|---|
| Base change => new candidate | old reviews/checks are stale for the candidate |
| No Git conflict != semantic compatibility | review base diff touching feature modules |
| `pending` / `missing` check | not success; PASS impossible |
| Conflict changes behavior | `route_to: OPTIONS_DRAFT` |
| Code mistake in resolution or regression | `route_to: BUILD` |
| Integration rebuilds | max 2 per feature, then merge stays blocked with a concrete problem |

## Typical interaction risks

- Base migration renames/drops a column or index the feature uses.
- Two features register the same route, permission or config key.
- Lockfile or dependency version drift changes behavior of a library the feature uses.
- Shared contract (API schema, event) changed on base; feature consumes the old shape.
- Test fixtures/seeds changed on base, making feature tests pass vacuously.
