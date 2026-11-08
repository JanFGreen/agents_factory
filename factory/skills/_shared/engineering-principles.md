# Engineering principles

Concrete rules behind KISS, DRY, SOLID and modularity, written so that each one can be
checked. Every rule has an ID; findings cite it (`EP-4: ...`). The repository's own rules
(`AGENTS.md`, `docs/ENGINEERING.md` or equivalent) are more specific and win on conflict.
The EP-5 ladder, EP-20, EP-21 and the lean pass are adapted from ponytail
(github.com/DietrichGebert/ponytail, MIT, Copyright (c) 2026 DietrichGebert).

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
| EP-5 | **Smallest complete change (KISS, YAGNI).** Before writing new code, take the first step that fully works: (1) is it needed now (acceptance or a `required_now` constraint)? Leave the rest out and name it under `Skipped:` in the handoff; (2) the codebase already has it (a helper, component, service, pattern): use it the way the surrounding code does; (3) the language's standard library or the platform has it; (4) an installed dependency has it (never add one for a few lines, EP-12); (5) one line a reader gets at a glance; (6) only then the minimum new code that works. No abstraction, option, flag, wrapper, type conversion or hook without two current uses or a `required_now` need; keep values in the form the platform already gives. Prefer data to configuration and deletion to addition. Lazy about the solution, never about the change: every caller, test and fixture the change breaks is part of it. A class is not an abstraction in this sense: EP-15 decides between a class and functions. | review |
| EP-6 | **Explicit dependencies.** Time, randomness, I/O, environment and network come in as parameters or ports. No module-level mutable state, no stateful singletons. | review |
| EP-7 | **Validate at the boundary.** External input (HTTP, files, storage, env, other services) is parsed once at the edge into typed values; inside, types are trusted. | review |
| EP-8 | **Errors are values or specific types.** Expected failures are typed results or specific errors and are never swallowed; programmer errors fail fast. | review |
| EP-9 | **Domain names.** Names come from the product language. No `utils`, `helpers`, `common`, `manager` dumping grounds. | review |
| EP-10 | **Extend by adding (open/closed).** A new variant is a new unit plus one registration. A change that must edit the same `switch`/`if` chain in several places is a defect: introduce a strategy or a table. | review |
| EP-11 | **Honest interfaces (SOLID L, I, D).** Every implementation of a port honours the whole contract (no "not supported" stubs). Interfaces are small and shaped by their callers. Callers depend on the port, not on a concrete adapter. | review |
| EP-12 | **Current APIs, few dependencies.** Use the API of the installed version: read its type definitions or docs in the repo, not memory. Deprecated APIs are errors. A new runtime dependency is never added silently; the task must name it. | gate (deprecation lint, protected lockfile), review |
| EP-13 | **Performance hygiene.** No query or request inside a loop over data (N+1). No unbounded list, query or payload (paginate, limit). No repeated expensive work per render or per request that can be derived once. No blocking I/O on a hot path. | review (gate when the profile has a benchmark or bundle budget) |
| EP-14 | **Testable core.** Domain logic sits in domain objects (or pure functions where EP-15 allows them) with I/O behind ports, so tests need no network, real clock or sleeps. | review (test-adequacy checks the tests) |
| EP-15 | **Object-oriented by default.** State and the behaviour that keeps it valid live together in a class: a domain object guards its invariants in its constructor and methods, callers ask it to act instead of reading its fields and deciding for it (`board.place(cell)`, not `applyMove(board, cell)` next to a bare record). Services, repositories and adapters are classes that receive their dependencies in the constructor (EP-6). Prefer immutable objects where the framework needs value semantics (a method returns a new instance), e.g. React state. Plain functions are right for stateless transformations, small helpers and framework idioms built on functions (React components and hooks, reducers that delegate to domain objects). A functional design elsewhere needs a reason in the handoff, e.g. the problem is a pipeline of transformations. The repository's established style wins over this default. | review |
| EP-16 | **Atomic decisions.** Everything a write depends on (permission, plan, quota, balance, version) is read inside the same transaction or lock as the write, at one captured instant. A transaction API runs synchronous work only: its type rejects a callback that returns a Promise, or it awaits the work before committing. | review (test-adequacy checks that a test fails without the transaction) |
| EP-17 | **Fetched state has a lifecycle.** State read from a server or store has explicit states (loading, ready, failed with a retry) and named refresh events (a successful sync, a session change, focus or foreground, a known expiry). Fetching once on mount is a defect when the value can change while the screen stays mounted. | review |
| EP-18 | **Fail closed on invalid data.** Config and stored values that grant access (limits, plans, expiry dates) are validated where they enter: at startup, and at the repository read. NaN, Infinity, negative or fractional limits and unparsable dates are rejected or mean "no access", never "unlimited" or "never expires". | review |
| EP-19 | **Abstractions keep what the caller needs.** A function returns the whole value its callers decide on (the effective grant `{ plan, expiresAt }`, not only the plan id), so no caller reconstructs the rest by guessing. A compatibility projection is named for what it projects (`legacyPremiumMarker`), not for the concept it replaces. | review |
| EP-20 | **Fix at the source.** Before changing a function's behaviour, signature or return value, find every caller (grep) and keep each one working. A bug in shared code is fixed once there, not patched in the one caller that showed it. Code that is moved or merged keeps its validation and error handling. | review |
| EP-21 | **Named shortcuts.** A limit taken on purpose to keep the change small (a linear scan over a list capped at 50 items, an in-process cache while the app runs on one server, one retry) carries a comment at that line: `shortcut: <the limit>, <when to upgrade>`. It is a recorded decision, not a defect, until the expected load, input or a requirement crosses the limit. It never covers anything from "What lean never cuts". | review |

**What lean never cuts** (EP-5 and EP-21 stop here): validation at a trust boundary (EP-7),
error handling that prevents data loss (EP-8, EP-16), security, accessibility, fail-closed
checks (EP-18), the tests the test plan names, and anything the task asks for. Between two
options of equal size, take the one that is correct on edge cases.

## Design patterns

Use a pattern when the code has the problem it solves now, in this change, not to look
designed or for a variant that may come later (EP-5). These suit code written and changed by
agents because they keep every change local and explicit. A port earns its place with a
second implementation or with a test that would otherwise need the network, a real clock,
randomness or a real store (EP-14); otherwise call the module directly.

| Problem in the code | Pattern |
|---|---|
| Logic tangled with I/O or a framework | Domain objects free of I/O at the core, I/O at the edge; ports and adapters (hexagonal) |
| Rules about one concept scattered over free functions taking the same record | Domain object (class) that owns the record and those rules (EP-15) |
| A variant changes behaviour (a difficulty, a payment method) | Polymorphism: one class per variant behind a small interface (strategy) |
| The same `switch` on a kind in several places; a new kind edits many files | Strategy or a registry (table of handlers); discriminated union with an exhaustive match |
| Storage details leak into business code | Repository (port) with a real adapter and an in-memory fake for tests |
| Many-step UI or process state with flags (`isLoading && !error && ...`) | State machine with explicit transitions (a class with one method per event; in React, a reducer that delegates to it) |
| A feature spread over every layer folder | Vertical slice: one folder per feature, a thin shared kernel |
| Building an object needs many optional parameters | Options object with defaults; builder only for genuinely complex construction |
| A third-party API shape spreads through the code | Adapter (anti-corruption layer) at the boundary |
| Expected failure handled by exceptions far away | Result type / typed errors returned to the caller |

**Over-engineering (EP-5 findings):** an interface or abstract class with one
implementation and no test fake; a factory that builds one type; a plugin system with one
plugin; inheritance deeper than one level; an event bus or observer where a direct call
would do; generic "base" classes for code that is not yet repeated; a wrapper that only
renames or forwards; a conversion of a value the platform already gives in the needed form;
a new helper next to an existing one that does the same; a dependency for what the standard
library does; an option or setting nobody sets.

**Avoid in agent-written code:** hidden control flow (global event buses, decorators or
metaprogramming that change behaviour, monkey-patching); god objects; service locators;
mutable singletons. An agent reads a small slice of the code; behaviour it cannot see
from that slice is behaviour it will break.

## For reviewers

- Report a *review* rule only with the concrete location and the cost it causes ("TASK-05
  will have to edit both copies", "every new mode edits three switches").
- Lean pass (EP-5): look for code that need not exist and name the smaller thing, in one of
  these classes: *delete* (dead code, an unused export, option or parameter, a speculative
  feature), *reuse* (the repo already has it: name the path), *stdlib* (the standard library
  or platform does it, or a new dependency does a few lines' work), *yagni* (an abstraction
  with one implementation, a setting nobody sets), *merge* (near-copies that must change
  together). Each needs the location and what it costs; no case, no finding.
- A `shortcut:` comment (EP-21) is a decision: report it only when the expected load, input
  or a requirement already crosses the limit it names.
- Blocking: EP-1, EP-4 (same rule twice), EP-6 (hidden global state), EP-10, EP-13, EP-16, EP-17
  and EP-18 when the diff introduces the problem on a path users or later tasks will hit. Other rules are
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
