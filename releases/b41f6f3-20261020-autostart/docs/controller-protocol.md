# Controller protocol v1

The business backend calls the C++ controller over HTTP. JSON requests and
responses use UTF-8, `Content-Type: application/json`, and optional
`Authorization: Bearer <CONTROLLER_TOKEN>`. A non-loopback listener requires
a token of at least 16 characters. Host must match the listener or an explicit
`--allow-host`. Browser Origin headers are rejected; there is no controller CORS.

## State and device control

| Method | Path | Body |
| --- | --- | --- |
| GET | `/v1/state` | none |
| GET | `/v1/ports` | none |
| POST | `/v1/control/connect` | `{"port":"COM5"}` or an enumerated Linux path |
| POST | `/v1/control/disconnect` | `{}` |
| POST | `/v1/control/check` | `{"metadata":{"machine_paper":[210,297]}}` |
| POST | `/v1/control/start` | `{"job_id":"32-lowercase-hex","origin_confirmed":true}` |
| POST | `/v1/control/pause` | `{}` |
| POST | `/v1/control/resume` | `{}` |
| POST | `/v1/control/stop` | `{}` |
| POST | `/v1/control/jog` | `{"axis":"X","distance":1,"feed":300}` |
| POST | `/v1/control/pen` | `{"position":"up"}` or `"down"` |
| POST | `/v1/control/manual-pause` | `{}` |
| POST | `/v1/control/manual-stop` | `{}` |

Control responses are snapshots with `protocol_version:1`, `controller:"cpp"`,
`connected`, `phase`, `mode`, `port`, `job_id`, `operation_type`, `acknowledged`,
`total`, `status`, `position`, `pen`, `pen_top`, `motion_revision`, `last_action`
and `error`. Positions come from controller MPos, not a pen sensor. Progress
counts acknowledged commands; it is not physical path completion.

Phases include `disconnected`, `idle`, `manual`, `manual_stopping`, `running`,
`paused`, `draining`, `complete`, `stopping`, `stopped`, `failed`. The serial
stream has one reader; HTTP state requests cannot compete with motion reads.
Realtime controls use a separate short write lock and bypass upload/query locks.

## Upload before execution

ID is exactly 32 lowercase hexadecimal characters. No client filesystem paths
are accepted. Each JSON request is bounded to 128 KiB; each chunk's decoded
content is at most 32 KiB. Task size limit is 32 MiB.

1. `POST /v1/jobs/<id>/begin` with execution metadata:

   ```json
   {"sha256":"64-lowercase-hex", "commands":100,
    "machine_paper":[210,297], "machine_page_bounds":[0,0,210,297]}
   ```

2. Response: `{job_id,offset,ready,consumed}`. Seek to offset in the local task
   file. `POST /v1/jobs/<id>/chunk` with `{"offset":0,"content":"G21\n..."}`.
   Content is printable ASCII and LF. The returned offset is the stored size.
   Retrying the same already-stored bytes is accepted; overlaps/conflicts fail.
3. `POST /v1/jobs/<id>/seal` with `{}` verifies streamed SHA-256, command count,
   command grammar and bounds. A failed seal cannot be executed.
4. Call start. The controller revalidates the file and device, flushes an
   exclusive execution marker to disk, then launches a local worker.

Begin is idempotent only with identical metadata. Ready/consumed tasks cannot be
modified. Same-process duplicate start returns the current snapshot if the ID
matches the current task. An older or restarted-process consumed ID is rejected;
it is never replayed. There is no automatic resume or command replay after a
timeout. A new task requires a new ID and renewed origin confirmation.

Only the generator's explicit header (`G21`, `G90`, `G94`, `G1 Z0 F3000`,
`G92 X0 Y0`), bounded `G1 X... Y... F...` / `G1 Z0|10 F...` moves, and the
final Z0 / X0 Y0 return are accepted. Arbitrary GRBL settings, unlock, homing,
and raw G-code execution endpoints are not provided.

## Failure and lifetime

An accepted task survives browser/backend loss because its complete file is
local. Serial errors/alarms stop further commands and request hold. Task stop
requests hold plus reset and closes the port. No automatic lift or origin return
occurs after failure/stop. Normal completion waits for physical Idle and checks
MPos against G92 XY and Z0.

401 indicates authentication failure, 403 an untrusted Host/Origin, 400 invalid
JSON/schema, 409 a conflicting state/device/upload condition, 413 oversized
request, 415 wrong content type. Error bodies contain `error`. The business API
reports unreachable controllers as 503 and continues to generate previews.

Stop/finish and disconnect before terminating the controller. SIGINT/SIGTERM
use a graceful listener shutdown and the destructor requests hold for active
motion, but forced process termination or power loss cannot promise delivery.
Never delete `consumed` markers to make a machine task replay.
