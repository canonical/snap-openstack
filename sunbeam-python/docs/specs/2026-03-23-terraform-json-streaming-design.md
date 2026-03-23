# Terraform JSON Streaming Output Design

## Problem

`TerraformHelper.apply()`, `destroy()`, and `sync()` use `subprocess.run()` with
`capture_output=True`, blocking until terraform finishes (up to 20 min). During
execution there is zero visibility into progress — the user sees only a Rich spinner.
stdout/stderr are logged at DEBUG level after completion.

There is no way to tell if terraform is stuck, which resource it's working on, or how
far along it is. The existing juju progress pattern (`_UpdateStatusThread` + `queue.Queue`)
is an ad-hoc solution for a similar problem that could benefit from unification.

## Goals

1. Stream terraform's native `-json` output in real-time during `apply`, `destroy`,
   and `sync`.
2. Display a 3-line rolling window of recent terraform events above the Rich spinner.
3. Log all events as structured JSON at DEBUG level for programmatic consumers.
4. Introduce a unified progress event protocol that both terraform and (future) juju
   progress can use.
5. Design for smart timeouts (inactivity-based + total ceiling) without implementing
   them now.

## Non-Goals

- Migrating the existing juju `_UpdateStatusThread` / `queue.Queue` pattern (future work).
- Implementing smart timeouts (future work, but the design must support it).
- Changing terraform plan output or any non-apply/destroy/sync commands.

## Design

### 1. Event Protocol (`sunbeam/core/progress.py`)

A new module containing the event model and reporter abstractions.

#### ProgressEvent

```python
@dataclass
class ProgressEvent:
    source: str          # "terraform", "juju", etc.
    event_type: str      # "apply_start", "apply_complete", "app_ready", etc.
    message: str         # Human-readable summary
    timestamp: datetime  # When the event occurred
    metadata: dict       # Raw data (full terraform JSON object, app name, etc.)
```

#### ProgressReporter Protocol

```python
class ProgressReporter(Protocol):
    def report(self, event: ProgressEvent) -> None: ...
```

#### Concrete Reporters

**`LoggingProgressReporter`**: Writes each event as a structured JSON line to the
Python logger at DEBUG level. No filtering — every event type is logged.

**`RichProgressReporter`**: Maintains a 3-line `collections.deque` of recent event
messages. On each `report()`, builds a Rich `Group` renderable containing the rolling
window lines as `Text` objects followed by the spinner's original status message, and
passes it to `Status.update()`. This renders the 3 most recent events as static lines
above the animated spinner. Receives the `Status` object and the step's base status
message at construction time.

Example terminal output:
```
  juju_application.nova-compute: creating...
  juju_application.keystone: created (4.2s)
  juju_application.glance: creating...
⠋ Deploying OpenStack ...
```

**Filtering responsibility**: Each event source (terraform, future juju) is responsible
for filtering which events it reports. The reporter displays whatever it receives.

**Thread safety**: Reporters are not required to be thread-safe in this design. The
terraform streaming path is single-threaded (synchronous line-by-line reading). If
future juju integration calls `report()` from background threads, thread safety must
be added at that point (e.g., a lock in `CompositeProgressReporter`).

**`CompositeProgressReporter`**: Fans out `report()` calls to multiple reporters.

```python
class CompositeProgressReporter:
    def __init__(self, *reporters: ProgressReporter):
        self.reporters = reporters

    def report(self, event: ProgressEvent) -> None:
        for reporter in self.reporters:
            reporter.report(event)
```

**`NoOpReporter`**: Does nothing. Used when steps are run outside `run_plan()` (tests,
scripts). Test code that currently calls `step.run(None)` should use
`step.run(StepContext(status=mock_status, reporter=NoOpReporter()))`.

### 2. StepContext (`sunbeam/core/common.py`)

Bundles cross-cutting concerns passed to every step, replacing the current
`status: Status | None` parameter.

```python
@dataclass
class StepContext:
    status: Status
    reporter: ProgressReporter
```

All fields are non-nullable. `run_plan()` always constructs a full context. If a caller
doesn't want reporting, it passes a `NoOpReporter`.

### 3. BaseStep Changes (`sunbeam/core/common.py`)

- `run(self, status: Status | None) -> Result` becomes
  `run(self, context: StepContext) -> Result`
- `is_skip(self, status: Status | None)` becomes
  `is_skip(self, context: StepContext)`
- `update_status(self, status, msg)` becomes
  `update_status(self, context: StepContext, msg: str)` — reads `context.status`
  internally

This is a mechanical change across ~42 files for `run()` and ~28 files for `is_skip()`:
replace `status` parameter with `context`, replace `status.update(...)` with
`context.status.update(...)`. The change is atomic — all overrides must be updated in
the same commit since `run_plan()` will pass `StepContext` to all steps.

### 4. Terraform Streaming (`sunbeam/core/terraform.py`)

#### `_run_terraform_command()`

A new private method that extracts the shared logic from `apply()`, `destroy()`, and
`sync()`:

- Accepts: command args, env overrides, reporter, timeout
- Uses `subprocess.Popen()` with `stdout=PIPE, stderr=PIPE`
- Reads stdout line-by-line in the main thread
- **Reads stderr in a separate daemon thread** that accumulates into a list, avoiding
  pipe buffer deadlock (if terraform writes enough stderr to fill the 64KB OS pipe
  buffer while Python blocks on stdout readline, the process would deadlock)
- Parses each stdout line as JSON
- Calls `_parse_terraform_event()` to translate into `ProgressEvent`
- Reports UI-relevant events via `reporter.report()`
- After stdout is exhausted, joins the stderr thread and collects accumulated output
- Checks process return code; raises `TerraformException` or
  `TerraformStateLockedException`

**State lock detection**: With `-json` output, terraform emits lock errors as
`diagnostic` type JSON events on stdout (with `severity: "error"` and a message
containing lock information). `_parse_terraform_event()` checks for diagnostic events
with lock-related messages and sets a flag. After the process exits with non-zero,
`_run_terraform_command()` raises `TerraformStateLockedException` if the flag is set,
or `TerraformException` otherwise. As a fallback, the accumulated stderr is also
checked for the `"remote state already locked"` string, preserving the existing
detection mechanism.

#### `_parse_terraform_event()`

Translates terraform's JSON objects into `ProgressEvent` instances. Only reports
UI-relevant event types:

| Terraform `@type`   | `event_type`      | Example message                                          |
|----------------------|-------------------|----------------------------------------------------------|
| `apply_start`        | `apply_start`     | `juju_application.keystone: creating...`                 |
| `apply_complete`     | `apply_complete`  | `juju_application.keystone: created (4.2s)`              |
| `apply_errored`      | `apply_errored`   | `juju_application.keystone: error: <diagnostic>`         |
| `change_summary`     | `change_summary`  | `Apply complete! Resources: 3 added, 1 changed, 0 destroyed.` |

Other event types (`refresh_start`, `refresh_complete`, `planned_change`, etc.) are
skipped for UI reporting but still present in the raw terraform log file via
`TF_LOG_PATH`.

#### Sample terraform JSON output

For implementer reference, `terraform apply -json` emits one JSON object per line:

```json
{"@level":"info","@message":"juju_application.keystone: Creating...","@module":"apply","@timestamp":"2026-03-23T10:00:01.000Z","type":"apply_start","hook":{"resource":{"addr":"juju_application.keystone","module":"","resource":"juju_application.keystone","implied_provider":"juju","resource_type":"juju_application","resource_name":"keystone","resource_key":null},"action":"create"}}
{"@level":"info","@message":"juju_application.keystone: Creation complete after 4s","@module":"apply","@timestamp":"2026-03-23T10:00:05.000Z","type":"apply_complete","hook":{"resource":{"addr":"juju_application.keystone","module":"","resource":"juju_application.keystone","implied_provider":"juju","resource_type":"juju_application","resource_name":"keystone","resource_key":null},"action":"create","elapsed_seconds":4}}
{"@level":"info","@message":"Apply complete! Resources: 3 added, 1 changed, 0 destroyed.","@module":"apply","@timestamp":"2026-03-23T10:00:06.000Z","type":"change_summary","changes":{"add":3,"change":1,"import":0,"remove":0,"operation":"apply"}}
```

State lock error (diagnostic type):
```json
{"@level":"error","@message":"Error acquiring the state lock","@module":"apply","@timestamp":"2026-03-23T10:00:00.000Z","type":"diagnostic","diagnostic":{"severity":"error","summary":"Error acquiring the state lock","detail":"state blob is already locked"}}
```

#### Command construction

All three methods add `-json` to the terraform command. All existing flags and dynamic
arguments (`extra_args`, `parallelism`, `-input=false`, etc.) are preserved unchanged.
Examples below show only the `-json` addition for clarity:

```python
# apply (also has: extra_args, -no-color, parallelism)
cmd = [self.terraform, "apply", "-json", "-auto-approve", "-no-color"]
# destroy (also has: -no-color, -input=false, parallelism)
cmd = [self.terraform, "destroy", "-json", "-auto-approve"]
# sync
cmd = [self.terraform, "apply", "-json", "-refresh-only", "-auto-approve"]
```

Note: `-json` affects stdout format only. `TF_LOG_PATH` file logging (set via
environment) continues to work as before.

#### Method signatures

`apply()`, `destroy()`, and `sync()` gain `reporter: ProgressReporter | None = None`.
When `None`, `_run_terraform_command()` still streams (to avoid pipe buffer blocking)
but discards events.

`update_tfvars_and_apply_tf()` and `update_partial_tfvars_and_apply_tf()` also gain
`reporter` and thread it through to `apply()`.

### 5. Integration with `run_plan()` (`sunbeam/core/common.py`)

```python
for step in plan:
    with console.status(step.status) as status:
        rich_reporter = RichProgressReporter(status)
        logging_reporter = LoggingProgressReporter()
        reporter = CompositeProgressReporter(rich_reporter, logging_reporter)
        context = StepContext(status=status, reporter=reporter)

        # ... prompt, is_skip, run all receive context
        result = step.run(context)
```

### 6. Terraform step integration

Steps like `DeployMachineApplicationStep.run()` pass `context.reporter` to
`self.tfhelper.update_tfvars_and_apply_tf(...)`. The tfhelper threads it to
`_run_terraform_command()`.

**`destroy()` direct callers**: `destroy()` is called directly (not through
`update_tfvars_and_apply_tf()`) in several places:
- `sunbeam/features/observability/feature.py`
- `sunbeam/features/interface/v1/openstack.py`
- `sunbeam/features/instance_recovery/consul.py`
- `sunbeam/steps/openstack.py`

These call sites are inside `run()` methods that will receive `StepContext`. They must
pass `context.reporter` to `self.tfhelper.destroy(reporter=context.reporter)`.

**Behavior change**: `destroy()` currently does **not** detect
`TerraformStateLockedException` — it always raises `TerraformException`. After this
change, `_run_terraform_command()` will detect state locks for all three methods
uniformly. This is an intentional improvement. Callers of `destroy()` that want retry
behavior can add `tenacity` decorators like `apply()` callers already have.

**`sync()` note**: `sync()` currently has no callers in the codebase. It is included
for consistency since the method exists, but there are no call sites to update and no
way to integration-test it.

### 7. Future: Smart Timeouts

The streaming `_run_terraform_command()` naturally supports activity-based timeouts:

- Track `last_event_time = time.monotonic()` on each line read
- In the read loop: if `time.monotonic() - last_event_time > INACTIVITY_TIMEOUT`,
  kill the process and raise `TerraformException`
- Keep a total ceiling via the existing `TERRAFORM_APPLY_TIMEOUT` mechanism as a
  safety net

**Not implemented in this change**, but the streaming architecture makes it trivial
to add later.

### 8. Future: Juju Progress Migration

The existing `_UpdateStatusThread` + `queue.Queue` pattern can be migrated to use
`ProgressReporter`:

- `wait_application_ready` accepts a `ProgressReporter` and calls `reporter.report()`
  for each app readiness event
- The `RichProgressReporter` handles display
- The background thread and queue become unnecessary

**Not in scope for this change**, but the protocol is designed for it.

## Files Changed

**Core changes (new logic):**

| File | Change |
|------|--------|
| `sunbeam/core/progress.py` | **New** — `ProgressEvent`, `ProgressReporter`, `LoggingProgressReporter`, `RichProgressReporter`, `CompositeProgressReporter`, `NoOpReporter` |
| `sunbeam/core/common.py` | Add `StepContext`, update `BaseStep.run()`/`is_skip()`/`update_status()` signatures, update `run_plan()` to construct context and reporters |
| `sunbeam/core/terraform.py` | Add `_run_terraform_command()`, `_parse_terraform_event()`, refactor `apply()`/`destroy()`/`sync()` to use streaming, add `-json` flag, thread `reporter` through |

**Mechanical `status` -> `context` signature migration (~42 files for `run()`, ~28 for `is_skip()`):**

| File pattern | Change |
|------|--------|
| `sunbeam/core/steps.py` | Update `run()`/`is_skip()` overrides, pass `context.reporter` to tfhelper |
| `sunbeam/steps/*.py` | `status` -> `context` parameter rename in `run()`/`is_skip()` overrides |
| `sunbeam/features/**/steps.py` | Same `status` -> `context` rename |
| `sunbeam/features/**/feature.py` | Same rename (several features have step classes in feature.py) |
| `sunbeam/commands/*.py` | Same rename where commands define inline step classes |

**`destroy()` direct callers (must thread `context.reporter`):**

| File | Change |
|------|--------|
| `sunbeam/features/observability/feature.py` | Pass `reporter` to `destroy()` |
| `sunbeam/features/interface/v1/openstack.py` | Pass `reporter` to `destroy()` |
| `sunbeam/features/instance_recovery/consul.py` | Pass `reporter` to `destroy()` |
| `sunbeam/steps/openstack.py` | Pass `reporter` to `destroy()` |

**Tests:**

| File | Change |
|------|--------|
| `tests/unit/sunbeam/core/test_terraform.py` | Update tests for streaming, add tests for event parsing |
| `tests/unit/sunbeam/core/test_common.py` | Update for `StepContext` |
| `tests/unit/**/test_*.py` | Update mocks for `run(context)` signature |

## Testing Strategy

- **Unit tests for `_parse_terraform_event()`**: Feed sample terraform JSON lines,
  assert correct `ProgressEvent` output.
- **Unit tests for `_run_terraform_command()`**: Mock `Popen` with canned JSON output,
  assert reporter receives expected events. Test error cases (non-zero exit, state lock).
- **Unit tests for reporters**: Assert `RichProgressReporter` calls `status.update()`
  with correct rolling window content. Assert `LoggingProgressReporter` logs structured
  JSON.
- **Existing test updates**: Update mocks/signatures for the `StepContext` change.
