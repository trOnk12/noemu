# Driver protocol v1

The Python client sends one UTF-8 JSON object followed by LF per loopback TCP
connection. The driver serves requests serially and returns one JSON object plus
LF. Each direction is limited to 1 MiB, including the delimiter. Text is a JSON
string; embedded tabs, newlines, Unicode, and trailing punctuation are preserved.
The old text protocol is not accepted.

```json
{"version":1,"request_id":"example-1","command":"type","args":["Good input","one\ntwo"],"timeout_ms":120000}
```

Unknown commands, wrong arity, invalid directions, invalid numeric ranges, and
unexpected request fields fail. Both Python and Kotlin validate the request. The
server bounds incomplete request reads to ten seconds. The client applies an
absolute deadline, rather than extending it every time a peer sends another byte.

The result includes `version`, `request_id`, `identity` (instance, build, profile),
`status`, `error`, `text`, `observation`, `artifacts`, `timing`, `capabilities`, and
`limitations`. `status` is `passed`, `failed`, `unsupported`, or `inconclusive`.
A non-passed result carries an error code and message. The client rejects an
unsupported version, a mismatched request or instance identity, invalid JSON,
truncated replies, and contradictory success/error fields.

Current limits:

- Build identity is legacy metadata, or null for a direct start, not an immutable
  runtime generation. `profile` is `legacy-sdk36`.
- `passed` establishes completion of the requested operation; it does not imply
  undeclared assertions or cross-backend agreement.
- Observations contain visible semantic elements, full labels, enabled/editable
  state, bounds, selectors when available, and `observation-id:index` references.
  Numeric compatibility references require an unchanged element at that index.
- A cooperative driver deadline cannot forcibly interrupt arbitrary app code.
  The client times out and reports an unknown/partial outcome; retries are not
  automatic or guaranteed idempotent. Supervisor termination is milestone B.
- Exception attribution to app, runtime, or fixture is not inferred from stack
  package names. The failure remains visible pending investigation.
- The transport is local development infrastructure, not a security boundary
  against other programs running as the same OS user.

Shared Python definitions are in `noemu/protocol.py`; the driver implementation is
`noemu/driver/Protocol.kt`. Python malformed-response checks and Kotlin contract
tests cover the framing and validation boundary. Future replay/report interfaces
must consume the same result model rather than parse human-readable text.
