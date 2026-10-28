# Engineering principles

Concrete rules behind KISS, DRY, SOLID and modularity, written so that each one can be
checked. Every rule has an ID; findings cite it (`EP-4: ...`). The repository's own rules
(`AGENTS.md`, `docs/ENGINEERING.md` or equivalent) are more specific and win on conflict.

**Who checks what.** Rules marked *gate* are enforced by the trusted checks of the repo's
stack profile (below). A failing gate already rejects the change, so a reviewer does not
report them again. Rules marked *review* need judgement; only those are review findings.
Silencing a gate rule (`eslint-disable` for a limit, `@ts-nocheck`, `jscpd:ignore`,
`noqa: C901`, `#[allow(clippy::too_many_lines)]`, `CHECKSTYLE:OFF`) is a policy violation:
restructure the code instead.

## Rules

| ID | Rule | Check |
|---|---|---|
| EP-1 | **Layers.** Dependencies point inward: domain ← application ← adapters (UI, HTTP, storage). The domain has no I/O, framework or platform imports. | gate (layer lint), review for layers the lint does not cover |
| EP-2 | **One responsibility.** A module can be described in one sentence without "and". It exports only what its callers use. | review |
| EP-3 | **Small units.** Per stack profile; default: cyclomatic complexity ≤ 10, nesting ≤ 3, ≤ 4 parameters (more: one options object), function ≤ 60 lines, file ≤ 300 lines. | gate |
| EP-4 | **One home for each piece of knowledge.** No new copied block (≥ 50 tokens). A business rule, constant, schema or mapping lives in one place; two different implementations of the same rule are a defect even when the text differs. | gate (copies), review (same rule written twice) |
| EP-5 | **Simplest thing that works (KISS, YAGNI).** No abstraction, option, flag or hook without two current uses or a `required_now` need. Prefer a function to a class and data to configuration. | review |
| EP-6 | **Explicit dependencies.** Time, randomness, I/O, environment and network come in as parameters or ports. No module-level mutable state, no stateful singletons. | review |
| EP-7 | **Validate at the boundary.** External input (HTTP, files, storage, env, other services) is parsed once at the edge into typed values; inside, types are trusted. | review |
| EP-8 | **Errors are values or specific types.** Expected failures are typed results or specific errors and are never swallowed; programmer errors fail fast. | review |
| EP-9 | **Domain names.** Names come from the product language. No `utils`, `helpers`, `common`, `manager` dumping grounds. | review |
| EP-10 | **Extend by adding (open/closed).** A new variant is a new unit plus one registration. A change that must edit the same `switch`/`if` chain in several places is a defect: introduce a strategy or a table. | review |
| EP-11 | **Honest interfaces (SOLID L, I, D).** Every implementation of a port honours the whole contract (no "not supported" stubs). Interfaces are small and shaped by their callers. Callers depend on the port, not on a concrete adapter. | review |
| EP-12 | **Current APIs, few dependencies.** Use the API of the installed version: read its type definitions or docs in the repo, not memory. Deprecated APIs are errors. A new runtime dependency is never added silently; the task must name it. | gate (deprecation lint, protected lockfile), review |
| EP-13 | **Performance hygiene.** No query or request inside a loop over data (N+1). No unbounded list, query or payload (paginate, limit). No repeated expensive work per render or per request that can be derived once. No blocking I/O on a hot path. | review (gate when the profile has a benchmark or bundle budget) |
| EP-14 | **Testable core.** Logic sits in pure functions or behind ports, so tests need no network, real clock or sleeps. | review (test-adequacy checks the tests) |

## Design patterns

Use a pattern when the code has the problem it solves, not to look designed. These suit
code written and changed by agents because they keep every change local and explicit.

| Problem in the code | Pattern |
|---|---|
| Logic tangled with I/O or a framework | Functional core, imperative shell; ports and adapters (hexagonal) |
| The same `switch` on a kind in several places; a new kind edits many files | Strategy or a registry (table of handlers); discriminated union with an exhaustive match |
| Storage details leak into business code | Repository (port) with a real adapter and an in-memory fake for tests |
| Many-step UI or process state with flags (`isLoading && !error && ...`) | State machine or reducer with explicit transitions |
| A feature spread over every layer folder | Vertical slice: one folder per feature, a thin shared kernel |
| Building an object needs many optional parameters | Options object with defaults; builder only for genuinely complex construction |
| A third-party API shape spreads through the code | Adapter (anti-corruption layer) at the boundary |
| Expected failure handled by exceptions far away | Result type / typed errors returned to the caller |

**Over-engineering (EP-5 findings):** an interface or abstract class with one
implementation and no test fake; a factory that builds one type; a plugin system with one
plugin; inheritance deeper than one level; an event bus or observer where a direct call
would do; generic "base" classes for code that is not yet repeated.

**Avoid in agent-written code:** hidden control flow (global event buses, decorators or
metaprogramming that change behaviour, monkey-patching); god objects; service locators;
mutable singletons. An agent reads a small slice of the code; behaviour it cannot see
from that slice is behaviour it will break.

## For reviewers

- Report a *review* rule only with the concrete location and the cost it causes ("TASK-05
  will have to edit both copies", "every new mode edits three switches").
- Blocking: EP-1, EP-4 (same rule twice), EP-6 (hidden global state), EP-10 and EP-13 when
  the diff introduces the problem on a path users or later tasks will hit. Other rules are
  non-blocking (`medium` or `low`), and so is anything that existed before the diff.
- Do not report what the gate enforces (complexity, sizes, copies, layer imports,
  deprecations, formatting); do not ask for patterns the code does not need.

## Stack profiles

The same rules, the tools that enforce the *gate* rows, and the idioms that differ.

| Stack | EP-1 layers | EP-3 size/complexity | EP-4 copies | EP-12 deprecations, deps | Error idiom |
|---|---|---|---|---|---|
| TypeScript / Node | ESLint `no-restricted-imports` per folder or dependency-cruiser | ESLint `complexity`, `max-depth`, `max-params`, `max-lines-per-function`, `max-lines` | jscpd with a baseline (`--fail-on-new-clones`) | `@typescript-eslint/no-deprecated`; exact versions; `npm audit` for new deps | discriminated-union results; zod at the boundary |
| Python | import-linter contracts | ruff `C901` (mccabe), `PLR0913` (args), `PLR0915` (statements) | jscpd | ruff `UP` rules and warnings as errors in tests; `pip-audit` | specific exception types; pydantic at the boundary |
| Rust | crate/module visibility (`pub(crate)`), workspace crates per layer | clippy `cognitive_complexity`, `too_many_arguments`, `too_many_lines` with `-D warnings` | jscpd | `#[deny(deprecated)]`; cargo-deny (advisories, licences) | `Result` with `thiserror` types; no `unwrap` outside tests |
| Java / Kotlin | ArchUnit rules | PMD / Checkstyle (`CyclomaticComplexity`, `ParameterNumber`, `MethodLength`) | jscpd or PMD CPD | `-Werror -Xlint:deprecation`; OWASP dependency-check | checked domain exceptions or `Result`; Bean Validation at the boundary |

Limits that existing code already exceeds go into an explicit, protected baseline (a
legacy file list or a clone baseline). New code meets the limits; baselines only shrink.
