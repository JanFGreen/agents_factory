# The senior bar

Write code a senior engineer would put their name to: a teammate reads it once and
understands it, trusts it in production, and changes it next month without fear. Passing
the gate is the floor, not the bar. Before the handoff, read your whole diff top to bottom
as that engineer reviewing a stranger's pull request, and fix what they would send back.

## What they would send back

- **Unclear names.** Every module, class, method and variable says what it is in the
  product's language. If you need the task text to understand a name, rename it.
- **Clever code.** Plain code wins over a trick. One level of abstraction per function;
  no nested ternaries, no chains that need a comment to decode.
- **Two sources of truth.** The same state kept twice (a value and its copy in a ref, a
  cache that can drift, a flag derivable from other data) is a bug waiting for a timing
  window. Keep one and derive the rest.
- **Leftovers.** Dead code, commented-out code, unused exports or parameters, debug output,
  experiments that did not work. Delete them; version control remembers.
- **Careless failure paths.** Every failure is handled on purpose: the caller learns what
  happened, the user sees what to do next, nothing is swallowed or retried blindly. The
  edge cases are covered: empty, maximum, repeated, concurrent, slow.
- **Comments that repeat the code.** Comments say why (a constraint, a decision, a trap),
  never what the next line does. A public unit whose intent is not obvious gets one line.
- **Inconsistency.** Your code looks like the code around it: same structure, idioms,
  error handling and test style. A second way of doing an existing thing is a defect.
- **Tests nobody can trust.** Test names state behaviour (`rejects a nickname longer than
  20 characters`), each test fails for one reason, and tests check behaviour through the
  public surface, not implementation details (no assertions on private state, on the text
  of a stylesheet, or on call counts that do not matter). No sleeps, no order dependence,
  no copy-pasted fixtures.
- **A bigger diff than needed.** Every changed line serves the task. Unrelated
  reformatting, renames and "while I was here" changes go out.

## The test

If you would have to explain a part of the diff in the pull request description for a
reviewer to accept it, make the code explain itself or rewrite it. If you would be
embarrassed to see the diff on a screen in a team review, it is not done.

Record in the handoff (`Self-review:`) what the re-read changed, concretely
("merged the duplicated saving flag into the status; renamed `data` to `savedResult`"),
or `no changes`. Never a self-assessment like "clean" or "high quality".
