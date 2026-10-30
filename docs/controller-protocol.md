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
| POST | `/v1/control/prepare` | `{"power_cycled":true,"pen_on_paper":true,"bottom_confirmed":true}`; paper-contact startup only |
| POST | `/v1/control/check` | `{"metadata":{"machine_paper":[210,297]}}` |
| POST | `/v1/control/start` | `{"job_id":"32-lowercase-hex","origin_confirmed":true}` |
| POST | `/v1/control/pause` | `{}` |
| POST | `/v1/control/resume` | `{}` |
| POST | `/v1/control/stop` | `{}` |
| POST | `/v1/control/speed` | `{"job_id":"current-job-id","percent":80}` |
| POST | `/v1/control/jog` | `{"axis":"X","distance":1,"feed":300}` |
| POST | `/v1/control/pen` | `{"position":"up"}` or `"down"` |
| POST | `/v1/control/manual-pause` | `{}` |
| POST | `/v1/control/manual-stop` | `{}` |

Control responses are snapshots with `protocol_version:1`, `controller:"cpp"`,
`connected`, `phase`, `mode`, `port`, `job_id`, `operation_type`, `acknowledged`,
`total`, `status`, `position`, `pen`, `pen_top`, `motion_revision`, `last_action`
and `error`. Positions come from controller MPos, not a pen sensor. Progress
counts acknowledged commands; it is not physical path completion.

Connect optionally accepts `device_control`, for example
`{"port":"COM5","device_control":{"controller_type":"grbl","pen_up":-1.25,"pen_down":-3.5,"pen_feed":200}}`.
Supported types are `kenjoy-grbl` (the original firmware/settings checks) and
`grbl` (no original-model firmware/direction/steps-per-mm requirement).
Omitted settings preserve the original type, Z0/Z10 and manual feed 600.
Z values must be finite within -300..300 mm, differ by at least 0.01 mm;
manual feed is 100..3000 mm/min. Values require physical device calibration.
Connection does not send motion. State includes the active `device_control`.
Control type and Z settings change only on a new connection.

With `PLOTTER_CONTACT_STARTUP=1`, use GRBL with logical pen-up Z0 and
pen-down Z13.5. Connection never moves the machine. The explicit `prepare`
action requires all three human confirmations, raises Z by 13.5 mm at
100 mm/min, waits for Idle and establishes a fresh work origin. Until it
finishes, jog/pen/check/start are rejected. Calibration is session-only and
is invalidated by disconnect, faults or an unexpected firmware restart.
State exposes `startup_mode`, `calibrated` and `logical_z`; `position` remains
machine MPos. The logical Z frame is translated consistently for manual pen,
writing, completion and controlled stop/recovery. See `multiple-controllers.md`.
In this mode only, connection soft-resets a stationary `Door:0` or `Hold:0`
to discard the previous queue, requires a fresh startup banner and unchanged
machine XYZ, then rechecks Idle. It never sends cycle resume. Other suspension
states and motion are rejected. `startup_reset:true` reports that recovery;
the session remains uncalibrated until human-confirmed preparation.

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

Execution metadata may include `device_control` and `device_port`. Z moves
must match the two configured positions; start rejects a mismatch with the
connected device settings or port. Old metadata retains the default Z0/Z10.

Only the generator's explicit header (`G21`, `G90`, `G94`, `G1 Z<up> F3000`,
`G92 X0 Y0`), bounded `G1 X... Y... F...` / configured Z moves (100..12000
mm/min), and the final configured pen-up / X0 Y0 return are accepted.
Arbitrary GRBL settings, unlock, homing,
and raw G-code execution endpoints are not provided.

## Failure and lifetime

An accepted task survives browser/backend loss because its complete file is
local. Serial errors/alarms stop further commands and request hold. Task stop
waits for physical hold, resets the controller, verifies coordinates, raises the
pen to its configured up position, then returns to the task XY origin. Return
can be cancelled; faults or failed position checks prevent further return
motion. Failure does not blindly lift or return. Normal completion waits for
physical Idle and checks MPos against the task XY origin and configured up Z.

401 indicates authentication failure, 403 an untrusted Host/Origin, 400 invalid
JSON/schema, 409 a conflicting state/device/upload condition, 413 oversized
request, 415 wrong content type. Error bodies contain `error`. The business API
reports unreachable controllers as 503 and continues to generate previews.

Stop/finish and disconnect before terminating the controller. SIGINT/SIGTERM
use a graceful listener shutdown and the destructor requests hold for active
motion, but forced process termination or power loss cannot promise delivery.
Never delete `consumed` markers to make a machine task replay.
