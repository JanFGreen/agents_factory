# Failure modes of effectful operations

For each operation in an `effect_sites` file answer the seven scenarios. `PASS` needs the line
that handles it or the test that proves it; `NOT_APPLICABLE` needs the reason.

| Scenario | Ask | Typical defect |
|---|---|---|
| `timeout` | What if the call never answers? Is there a deadline (`AbortSignal.timeout`, client timeout), and does the UI leave its pending state? | Spinner or disabled button forever |
| `cancellation` | Can the user or the code abandon it (navigation, new input)? Is the request aborted and its late answer ignored? | Late response overwrites newer state |
| `retry_idempotent` | If the same request is sent again (retry button, automatic retry, reload), is the effect applied once? Is there a request id, upsert or unique key? | Duplicate rows, double counting |
| `lost_response` | The server applied the write but the client saw an error (timeout, proxy 502, connection reset). What does the client show and do next? | "Failed" shown for a saved result, retry duplicates it |
| `double_submit` | Two clicks, Enter + click, two tabs: is the second one blocked or harmless? | Two writes from one action |
| `unmount_or_navigation` | The component unmounts or the user navigates away and back while the call or timer runs: is state set after unmount, and is the user's input (typed text, choices) kept or restored? | Lost input, state update on unmounted component, timer firing on the wrong screen |
| `partial_failure` | A multi-step effect stops halfway (first write ok, second fails): what remains, and is it consistent? | Half-written data, counters out of sync |

Probe tests: put them in the probe workspace next to the code, using the repo's test naming
and tools (fake timers, an injected `fetch` that never resolves or resolves twice, Fastify
`inject` twice with the same body). Assert the correct behavior, so the probe fails because of
the defect.
