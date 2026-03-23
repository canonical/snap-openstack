# Terraform JSON Streaming Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream terraform's native `-json` output in real-time through a unified ProgressReporter protocol, displaying a 3-line rolling window in the terminal and logging structured events.

**Architecture:** New `progress.py` module defines the event protocol and reporter implementations. `StepContext` replaces the bare `Status` parameter across all steps. `TerraformHelper` switches from `subprocess.run()` to `subprocess.Popen()` for line-by-line JSON streaming. `run_plan()` wires reporters into the step execution loop.

**Tech Stack:** Python 3, Rich (Status, Group, Text), subprocess.Popen, threading (for stderr), collections.deque, json, typing.Protocol

**Spec:** `docs/superpowers/specs/2026-03-23-terraform-json-streaming-design.md`

**Test runner:** `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit`

**Linting:** `cd sunbeam-python && tox -e pep8` and `cd sunbeam-python && tox -e mypy`

---

## File Structure

| File | Role |
|------|------|
| `sunbeam/core/progress.py` | **New** — ProgressEvent, ProgressReporter protocol, NoOpReporter, LoggingProgressReporter, RichProgressReporter, CompositeProgressReporter |
| `tests/unit/sunbeam/core/test_progress.py` | **New** — Tests for all reporter implementations |
| `sunbeam/core/common.py` | **Modify** — Add StepContext, update BaseStep.run()/is_skip()/update_status(), update run_plan() |
| `sunbeam/core/terraform.py` | **Modify** — Add _run_terraform_command(), _parse_terraform_event(), refactor apply()/destroy()/sync() |
| `tests/unit/sunbeam/core/test_terraform.py` | **Modify** — Add streaming tests, update existing mocks |
| 42 files in sunbeam/ (core/, steps/, features/, commands/, storage/, provider/) | **Modify** — Mechanical status->context migration in run()/is_skip() overrides |
| ~31 test files in tests/unit/ | **Modify** — Update step.run()/is_skip() calls to pass StepContext |

---

## Task 1: ProgressEvent and ProgressReporter Protocol

**Files:**
- Create: `sunbeam-python/sunbeam/core/progress.py`
- Create: `sunbeam-python/tests/unit/sunbeam/core/test_progress.py`

- [ ] **Step 1: Write tests for ProgressEvent and basic reporters**

Create `sunbeam-python/tests/unit/sunbeam/core/test_progress.py`:

```python
# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from datetime import datetime, timezone
from unittest.mock import Mock

from sunbeam.core.progress import (
    CompositeProgressReporter,
    LoggingProgressReporter,
    NoOpReporter,
    ProgressEvent,
)


class TestProgressEvent:
    def test_create_event(self):
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="juju_application.keystone: creating...",
            timestamp=datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc),
            metadata={"resource": "juju_application.keystone"},
        )
        assert event.source == "terraform"
        assert event.event_type == "apply_start"
        assert event.message == "juju_application.keystone: creating..."


class TestNoOpReporter:
    def test_report_does_nothing(self):
        reporter = NoOpReporter()
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="test",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={},
        )
        # Should not raise
        reporter.report(event)


class TestLoggingProgressReporter:
    def test_logs_event_as_json(self, caplog):
        reporter = LoggingProgressReporter()
        event = ProgressEvent(
            source="terraform",
            event_type="apply_complete",
            message="keystone: created",
            timestamp=datetime(2026, 3, 23, 10, 0, 5, tzinfo=timezone.utc),
            metadata={"elapsed": 4.2},
        )
        with caplog.at_level(logging.DEBUG, logger="sunbeam.core.progress"):
            reporter.report(event)
        assert len(caplog.records) == 1
        logged = json.loads(caplog.records[0].message)
        assert logged["source"] == "terraform"
        assert logged["event_type"] == "apply_complete"
        assert logged["message"] == "keystone: created"
        assert logged["metadata"] == {"elapsed": 4.2}


class TestCompositeProgressReporter:
    def test_fans_out_to_all_reporters(self):
        r1 = Mock()
        r2 = Mock()
        composite = CompositeProgressReporter(r1, r2)
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="test",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={},
        )
        composite.report(event)
        r1.report.assert_called_once_with(event)
        r2.report.assert_called_once_with(event)

    def test_empty_composite_does_not_raise(self):
        composite = CompositeProgressReporter()
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="test",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={},
        )
        composite.report(event)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_progress.py`
Expected: ImportError — `sunbeam.core.progress` does not exist yet

- [ ] **Step 3: Implement ProgressEvent, ProgressReporter, NoOpReporter, LoggingProgressReporter, CompositeProgressReporter**

Create `sunbeam-python/sunbeam/core/progress.py`:

```python
# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

LOG = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """A progress event from any source (terraform, juju, etc.)."""

    source: str
    event_type: str
    message: str
    timestamp: datetime
    metadata: dict


class ProgressReporter(Protocol):
    """Protocol for receiving progress events."""

    def report(self, event: ProgressEvent) -> None: ...


class NoOpReporter:
    """Reporter that does nothing. Used in tests or when no reporting is needed."""

    def report(self, event: ProgressEvent) -> None:
        pass


class LoggingProgressReporter:
    """Logs each event as structured JSON at DEBUG level."""

    def report(self, event: ProgressEvent) -> None:
        LOG.debug(
            json.dumps(
                {
                    "source": event.source,
                    "event_type": event.event_type,
                    "message": event.message,
                    "timestamp": event.timestamp.isoformat(),
                    "metadata": event.metadata,
                }
            )
        )


class CompositeProgressReporter:
    """Fans out report() calls to multiple reporters."""

    def __init__(self, *reporters: ProgressReporter):
        self.reporters = reporters

    def report(self, event: ProgressEvent) -> None:
        for reporter in self.reporters:
            reporter.report(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_progress.py`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd sunbeam-python
git add sunbeam/core/progress.py tests/unit/sunbeam/core/test_progress.py
git commit -m "feat: add ProgressEvent protocol and basic reporters

Introduces the progress event protocol with ProgressEvent dataclass,
ProgressReporter Protocol, NoOpReporter, LoggingProgressReporter,
and CompositeProgressReporter."
```

---

## Task 2: RichProgressReporter

**Files:**
- Modify: `sunbeam-python/sunbeam/core/progress.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/core/test_progress.py`

- [ ] **Step 1: Write tests for RichProgressReporter**

Append to `sunbeam-python/tests/unit/sunbeam/core/test_progress.py`:

```python
from sunbeam.core.progress import RichProgressReporter


class TestRichProgressReporter:
    def test_single_event_updates_status(self):
        status = Mock()
        reporter = RichProgressReporter(status, base_message="Deploying ... ")
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="keystone: creating...",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={},
        )
        reporter.report(event)
        status.update.assert_called_once()
        # The renderable passed to update should contain the event message

    def test_rolling_window_keeps_last_3(self):
        status = Mock()
        reporter = RichProgressReporter(status, base_message="Deploying ... ")
        for i in range(5):
            event = ProgressEvent(
                source="terraform",
                event_type="apply_start",
                message=f"resource-{i}: creating...",
                timestamp=datetime.now(tz=timezone.utc),
                metadata={},
            )
            reporter.report(event)
        assert status.update.call_count == 5
        # After 5 events, only the last 3 should be in the window
        assert len(reporter._recent_events) == 3
        messages = list(reporter._recent_events)
        assert messages[0] == "resource-2: creating..."
        assert messages[1] == "resource-3: creating..."
        assert messages[2] == "resource-4: creating..."

    def test_empty_base_message(self):
        status = Mock()
        reporter = RichProgressReporter(status, base_message="")
        event = ProgressEvent(
            source="terraform",
            event_type="apply_start",
            message="test: creating...",
            timestamp=datetime.now(tz=timezone.utc),
            metadata={},
        )
        reporter.report(event)
        status.update.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_progress.py::TestRichProgressReporter`
Expected: ImportError — `RichProgressReporter` not defined yet

- [ ] **Step 3: Implement RichProgressReporter**

Add to `sunbeam-python/sunbeam/core/progress.py`:

```python
from collections import deque

from rich.console import Group
from rich.status import Status
from rich.text import Text


class RichProgressReporter:
    """Displays a 3-line rolling window of events above the Rich spinner."""

    def __init__(self, status: Status, base_message: str, max_lines: int = 3):
        self._status = status
        self._base_message = base_message
        self._recent_events: deque[str] = deque(maxlen=max_lines)

    def report(self, event: ProgressEvent) -> None:
        self._recent_events.append(event.message)
        lines = [Text(f"  {msg}", style="dim") for msg in self._recent_events]
        renderable = Group(*lines, self._base_message)
        self._status.update(renderable)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_progress.py`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd sunbeam-python
git add sunbeam/core/progress.py tests/unit/sunbeam/core/test_progress.py
git commit -m "feat: add RichProgressReporter with rolling window display

Shows last 3 terraform events above the Rich spinner using
a Group renderable with dim-styled Text lines."
```

---

## Task 3: Atomic StepContext Migration (production code + tests)

> **IMPORTANT**: This is an atomic change. All production code overrides, `run_plan()`,
> and test files MUST be updated in a single commit. The spec explicitly states:
> "all overrides must be updated in the same commit since run_plan() will pass
> StepContext to all steps."

**Scope:** 42 production files with `run()` overrides, 28 with `is_skip()` overrides,
~31 test files. This is a large but mechanical change.

**Files — core:**
- Modify: `sunbeam-python/sunbeam/core/common.py` (StepContext + BaseStep + run_plan)
- Modify: `sunbeam-python/sunbeam/core/steps.py`
- Modify: `sunbeam-python/sunbeam/core/terraform.py` (TerraformInitStep)
- Modify: `sunbeam-python/sunbeam/core/manifest.py` (AddManifestStep)

**Files — steps/ (run grep to confirm full list):**
- `sunbeam/steps/microovn.py`
- `sunbeam/steps/openstack.py`
- `sunbeam/steps/hypervisor.py`
- `sunbeam/steps/k8s.py`
- `sunbeam/steps/juju.py`
- `sunbeam/steps/certificates.py`
- `sunbeam/steps/sso.py`
- `sunbeam/steps/mysql.py`
- `sunbeam/steps/clusterd.py`
- `sunbeam/steps/cluster_status.py`
- `sunbeam/steps/configure.py`
- `sunbeam/steps/maintenance.py`
- `sunbeam/steps/microceph.py`
- `sunbeam/steps/cinder_volume.py`
- `sunbeam/steps/bootstrap_state.py`
- `sunbeam/steps/sync_feature_gates.py`
- `sunbeam/steps/terraform.py`
- `sunbeam/steps/features.py`
- `sunbeam/steps/upgrades/intra_channel.py`
- `sunbeam/steps/upgrades/inter_channel.py`
- `sunbeam/steps/upgrades/base.py`

**Files — features/ and commands/ (run grep to confirm full list):**
- `sunbeam/features/baremetal/steps.py`
- `sunbeam/features/interface/v1/openstack.py`
- `sunbeam/features/instance_recovery/consul.py`
- `sunbeam/features/ldap/feature.py`
- `sunbeam/features/pro/feature.py`
- `sunbeam/features/observability/feature.py`
- `sunbeam/features/validation/feature.py`
- `sunbeam/features/caas/feature.py`
- `sunbeam/features/vault/feature.py`
- `sunbeam/features/tls/common.py`
- `sunbeam/features/shared_filesystem/manila_data.py`
- `sunbeam/commands/configure.py`
- `sunbeam/commands/proxy.py`
- `sunbeam/commands/generate_cloud_config.py`

**Files — storage/ and provider/ (run grep to confirm full list):**
- `sunbeam/storage/steps.py`
- `sunbeam/provider/maas/steps.py`
- `sunbeam/provider/local/steps.py`

**Test files (~31, run grep to confirm):**
- `tests/unit/sunbeam/conftest.py` (add `step_context` fixture)
- All test files that call `step.run()` or `step.is_skip()`

### Mechanical change instructions

For **every** file with `run()` or `is_skip()` overrides:

1. Add `from sunbeam.core.common import StepContext` if not already imported
2. Replace `def run(self, status: Status | None = None) -> Result:` with `def run(self, context: StepContext) -> Result:`
3. Replace `def is_skip(self, status: Status | None = None) -> Result:` with `def is_skip(self, context: StepContext) -> Result:`
4. Replace `self.update_status(status, msg)` with `self.update_status(context, msg)`
5. Replace bare `status.update(...)` calls with `context.status.update(...)`
6. Replace `super().run(status)` with `super().run(context)` — **check for this pattern** in:
   - `sunbeam/steps/maintenance.py` (3 calls)
   - `sunbeam/steps/cinder_volume.py` (1 call)
   - `sunbeam/steps/microceph.py` (1 call)
   - `sunbeam/features/shared_filesystem/manila_data.py` (1 call)
7. Replace `update_status_background(self, apps, queue, status)` with `update_status_background(self, apps, queue, context.status)` — found in ~15 call sites across `steps/` and `features/`

- [ ] **Step 1: Run grep to find ALL overrides across the entire codebase**

```bash
cd sunbeam-python
grep -rn "def run(self, status\|def is_skip(self, status" sunbeam/
grep -rn "super()\.run(status" sunbeam/
grep -rn "update_status_background(" sunbeam/
```

Use these outputs as the authoritative list of files and lines to modify.

- [ ] **Step 2: Add StepContext and update BaseStep + run_plan()**

In `sunbeam-python/sunbeam/core/common.py`, add the import and dataclass near the top (after existing imports), then update `BaseStep`:

Add import at top of file:
```python
from dataclasses import dataclass
from sunbeam.core.progress import (
    CompositeProgressReporter,
    LoggingProgressReporter,
    NoOpReporter,
    ProgressReporter,
    RichProgressReporter,
)
```

Add before `BaseStep` class:
```python
@dataclass
class StepContext:
    """Cross-cutting concerns passed to every step during plan execution."""

    status: Status
    reporter: ProgressReporter
```

Update `BaseStep` methods:
- `is_skip(self, status: Status | None = None)` → `is_skip(self, context: StepContext)`
- `run(self, status: Status | None)` → `run(self, context: StepContext)`
- `update_status(self, status: Status | None, msg: str)` → `update_status(self, context: StepContext, msg: str)` (read `context.status` inside)

Update `run_plan()` to construct `StepContext`:
```python
def run_plan(
    plan: Sequence[BaseStep],
    console: Console,
    no_hint: bool = True,
    no_raise: bool = False,
) -> dict:
    results = {}
    for step in plan:
        LOG.debug(f"Starting step {step.name!r}")
        with console.status(step.status) as status:
            rich_reporter = RichProgressReporter(status, step.status)
            logging_reporter = LoggingProgressReporter()
            reporter = CompositeProgressReporter(rich_reporter, logging_reporter)
            context = StepContext(status=status, reporter=reporter)

            if step.has_prompts():
                status.stop()
                step.prompt(console, no_hint)
                status.start()

            skip_result = step.is_skip(context)
            if skip_result.result_type == ResultType.SKIPPED:
                results[step.__class__.__name__] = skip_result
                LOG.debug(f"Skipping step {step.name}")
                continue

            if skip_result.result_type == ResultType.FAILED:
                if no_raise:
                    results[step.__class__.__name__] = skip_result
                    break
                raise click.ClickException(skip_result.message)

            LOG.debug(f"Running step {step.name}")
            result = step.run(context)
            results[step.__class__.__name__] = result
            LOG.debug(
                f"Finished running step {step.name!r}. Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            if no_raise:
                break
            raise click.ClickException(result.message)

    return results
```

- [ ] **Step 3: Migrate all production code overrides**

Work through each directory using the grep output from Step 1. Apply the mechanical changes listed above to every file. Do not skip any file.

- [ ] **Step 4: Add step_context fixture and migrate all test files**

In `sunbeam-python/tests/unit/sunbeam/conftest.py`, add:

```python
from sunbeam.core.common import StepContext
from sunbeam.core.progress import NoOpReporter


@pytest.fixture
def step_context():
    return StepContext(status=Mock(), reporter=NoOpReporter())
```

Then for every test file that calls `step.run()` or `step.is_skip()`:
1. Add `step_context` to the test function parameters (auto-discoverable pytest fixture)
2. Replace `step.run()` with `step.run(step_context)`
3. Replace `step.is_skip()` with `step.is_skip(step_context)`
4. Replace `step.run(Mock())` with `step.run(step_context)` (in `test_maintenance.py` and `provider/local/test_steps.py`)

Find all test files:
```bash
cd sunbeam-python
grep -rln "\.run()\|\.is_skip()\|\.run(Mock" tests/unit/
```

- [ ] **Step 5: Run full test suite**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/ -x`
Expected: All tests PASS (no functional changes, only signature migration)

- [ ] **Step 6: Run linting**

Run: `cd sunbeam-python && tox -e pep8`
Expected: PASS

- [ ] **Step 7: Commit (single atomic commit)**

```bash
cd sunbeam-python
git add sunbeam/ tests/
git commit -m "refactor: migrate BaseStep to StepContext parameter

Replaces status: Status | None parameter with StepContext across
BaseStep.run(), is_skip(), and update_status(). run_plan() now
constructs CompositeProgressReporter with Rich and logging reporters.
All step overrides, test files, and call sites updated atomically."
```

---

## Task 4: Terraform Event Parsing

**Files:**
- Modify: `sunbeam-python/sunbeam/core/terraform.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/core/test_terraform.py`

- [ ] **Step 1: Write tests for _parse_terraform_event()**

Add to `sunbeam-python/tests/unit/sunbeam/core/test_terraform.py`:

```python
import json
from datetime import datetime, timezone

from sunbeam.core.terraform import TerraformHelper


class TestParseTerraformEvent:
    """Tests for TerraformHelper._parse_terraform_event()."""

    def _make_helper(self, mocker, snap):
        """Create a minimal TerraformHelper for testing."""
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        return TerraformHelper(
            path=Path("/tmp/test"),
            plan="test-plan",
            tfvar_map={},
        )

    def test_apply_start_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "info",
            "@message": "juju_application.keystone: Creating...",
            "@timestamp": "2026-03-23T10:00:01.000Z",
            "type": "apply_start",
            "hook": {
                "resource": {
                    "addr": "juju_application.keystone",
                    "resource_type": "juju_application",
                    "resource_name": "keystone",
                },
                "action": "create",
            },
        })
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.source == "terraform"
        assert event.event_type == "apply_start"
        assert "keystone" in event.message
        assert "creating" in event.message.lower()

    def test_apply_complete_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "info",
            "@message": "juju_application.keystone: Creation complete after 4s",
            "@timestamp": "2026-03-23T10:00:05.000Z",
            "type": "apply_complete",
            "hook": {
                "resource": {
                    "addr": "juju_application.keystone",
                    "resource_type": "juju_application",
                    "resource_name": "keystone",
                },
                "action": "create",
                "elapsed_seconds": 4,
            },
        })
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "apply_complete"
        assert "keystone" in event.message
        assert "4" in event.message

    def test_apply_errored_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "error",
            "@message": "juju_application.keystone: error",
            "@timestamp": "2026-03-23T10:00:05.000Z",
            "type": "apply_errored",
            "hook": {
                "resource": {
                    "addr": "juju_application.keystone",
                },
                "action": "create",
            },
        })
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "apply_errored"

    def test_change_summary_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "info",
            "@message": "Apply complete! Resources: 3 added, 1 changed, 0 destroyed.",
            "@timestamp": "2026-03-23T10:00:06.000Z",
            "type": "change_summary",
            "changes": {"add": 3, "change": 1, "import": 0, "remove": 0},
        })
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "change_summary"
        assert "3 added" in event.message

    def test_diagnostic_state_lock_sets_flag(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "error",
            "@message": "Error acquiring the state lock",
            "@timestamp": "2026-03-23T10:00:00.000Z",
            "type": "diagnostic",
            "diagnostic": {
                "severity": "error",
                "summary": "Error acquiring the state lock",
                "detail": "state blob is already locked",
            },
        })
        state_lock_detected = [False]
        event = helper._parse_terraform_event(line, state_lock_flag=state_lock_detected)
        assert state_lock_detected[0] is True
        # Diagnostic events are not reported to UI
        assert event is None

    def test_unrecognized_type_returns_none(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps({
            "@level": "info",
            "@message": "Planning...",
            "@timestamp": "2026-03-23T10:00:00.000Z",
            "type": "planned_change",
        })
        event = helper._parse_terraform_event(line)
        assert event is None

    def test_invalid_json_returns_none(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        event = helper._parse_terraform_event("not valid json {{{")
        assert event is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_terraform.py::TestParseTerraformEvent -x`
Expected: AttributeError — `_parse_terraform_event` does not exist yet

- [ ] **Step 3: Implement _parse_terraform_event()**

Add to `sunbeam-python/sunbeam/core/terraform.py`:

At **module level** (near the top, after existing imports):
```python
from sunbeam.core.progress import ProgressEvent

# UI-relevant terraform event types
_TF_UI_EVENT_TYPES = {"apply_start", "apply_complete", "apply_errored", "change_summary"}
```

Then add this **method** inside the `TerraformHelper` class:

def _parse_terraform_event(
    self, line: str, state_lock_flag: list[bool] | None = None
) -> ProgressEvent | None:
    """Parse a terraform JSON line into a ProgressEvent.

    Returns None for non-UI-relevant events or unparseable lines.
    Sets state_lock_flag[0] = True if a state lock diagnostic is detected.
    """
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None

    event_type = data.get("type", "")

    # Check for state lock in diagnostic events
    if event_type == "diagnostic":
        diagnostic = data.get("diagnostic", {})
        summary = diagnostic.get("summary", "")
        detail = diagnostic.get("detail", "")
        if "state lock" in summary.lower() or "already locked" in detail.lower():
            if state_lock_flag is not None:
                state_lock_flag[0] = True
        return None

    if event_type not in _TF_UI_EVENT_TYPES:
        return None

    hook = data.get("hook", {})
    resource = hook.get("resource", {})
    addr = resource.get("addr", "unknown")
    action = hook.get("action", "unknown")
    timestamp_str = data.get("@timestamp", "")

    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        timestamp = datetime.now(tz=timezone.utc)

    if event_type == "apply_start":
        message = f"{addr}: {action}..."
    elif event_type == "apply_complete":
        elapsed = hook.get("elapsed_seconds", 0)
        message = f"{addr}: {action} complete ({elapsed}s)"
    elif event_type == "apply_errored":
        message = f"{addr}: {action} errored"
    elif event_type == "change_summary":
        message = data.get("@message", "Apply complete.")
    else:
        message = data.get("@message", "")

    return ProgressEvent(
        source="terraform",
        event_type=event_type,
        message=message,
        timestamp=timestamp,
        metadata=data,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_terraform.py::TestParseTerraformEvent`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd sunbeam-python
git add sunbeam/core/terraform.py tests/unit/sunbeam/core/test_terraform.py
git commit -m "feat: add terraform JSON event parser

Parses terraform -json output lines into ProgressEvent instances.
Filters to UI-relevant types: apply_start, apply_complete,
apply_errored, change_summary. Detects state lock from diagnostic events."
```

---

## Task 5: Terraform Streaming with _run_terraform_command()

**Files:**
- Modify: `sunbeam-python/sunbeam/core/terraform.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/core/test_terraform.py`

- [ ] **Step 1: Write tests for _run_terraform_command()**

Add to `sunbeam-python/tests/unit/sunbeam/core/test_terraform.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

from sunbeam.core.progress import NoOpReporter, ProgressEvent
from sunbeam.core.terraform import (
    TerraformException,
    TerraformStateLockedException,
)


class TestRunTerraformCommand:
    """Tests for TerraformHelper._run_terraform_command()."""

    def _make_helper(self, mocker, snap, tmp_path):
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        return TerraformHelper(
            path=tmp_path,
            plan="test-plan",
            tfvar_map={},
        )

    def test_successful_command_reports_events(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        json_lines = [
            json.dumps({
                "type": "apply_start",
                "@message": "creating",
                "@timestamp": "2026-03-23T10:00:01.000Z",
                "hook": {"resource": {"addr": "res.a"}, "action": "create"},
            }) + "\n",
            json.dumps({
                "type": "apply_complete",
                "@message": "created",
                "@timestamp": "2026-03-23T10:00:05.000Z",
                "hook": {
                    "resource": {"addr": "res.a"},
                    "action": "create",
                    "elapsed_seconds": 4,
                },
            }) + "\n",
        ]

        mock_process = MagicMock()
        mock_process.stdout = iter(json_lines)
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        reporter = Mock()

        with patch("subprocess.Popen", return_value=mock_process):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=reporter,
            )

        assert reporter.report.call_count == 2
        events = [call.args[0] for call in reporter.report.call_args_list]
        assert events[0].event_type == "apply_start"
        assert events[1].event_type == "apply_complete"

    def test_failed_command_raises_terraform_exception(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr.read.return_value = "Error: something failed"
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_state_lock_from_diagnostic_raises_state_lock_exception(
        self, mocker, snap, tmp_path
    ):
        helper = self._make_helper(mocker, snap, tmp_path)
        lock_line = json.dumps({
            "type": "diagnostic",
            "@level": "error",
            "@message": "Error acquiring the state lock",
            "@timestamp": "2026-03-23T10:00:00.000Z",
            "diagnostic": {
                "severity": "error",
                "summary": "Error acquiring the state lock",
                "detail": "state blob is already locked",
            },
        }) + "\n"

        mock_process = MagicMock()
        mock_process.stdout = iter([lock_line])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformStateLockedException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_state_lock_from_stderr_fallback(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr.read.return_value = "Error: remote state already locked"
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformStateLockedException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_none_reporter_still_works(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        json_line = json.dumps({
            "type": "apply_start",
            "@message": "creating",
            "@timestamp": "2026-03-23T10:00:01.000Z",
            "hook": {"resource": {"addr": "res.a"}, "action": "create"},
        }) + "\n"

        mock_process = MagicMock()
        mock_process.stdout = iter([json_line])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            # Should not raise even with no reporter
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=None,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_terraform.py::TestRunTerraformCommand -x`
Expected: AttributeError — `_run_terraform_command` does not exist yet

- [ ] **Step 3: Implement _run_terraform_command()**

Add to `sunbeam-python/sunbeam/core/terraform.py` in the `TerraformHelper` class:

```python
import subprocess
import threading
from datetime import timezone

from sunbeam.core.progress import ProgressReporter


def _run_terraform_command(
    self,
    cmd: list[str],
    env: dict,
    reporter: ProgressReporter | None = None,
    timeout: int = TERRAFORM_APPLY_TIMEOUT,
) -> None:
    """Run a terraform command with JSON streaming.

    Reads stdout line-by-line, parses JSON events, and reports them.
    Reads stderr in a separate thread to avoid pipe buffer deadlock.
    """
    LOG.debug(f"Running command {' '.join(cmd)}, cwd: {self.path}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=self.path,
        env=env,
    )

    # Read stderr in a background thread to prevent deadlock
    stderr_lines: list[str] = []

    def _read_stderr():
        assert process.stderr is not None
        stderr_lines.append(process.stderr.read())

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    # Read stdout line-by-line for JSON events
    state_lock_flag = [False]
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        event = self._parse_terraform_event(line, state_lock_flag)
        if event is not None and reporter is not None:
            reporter.report(event)

    # Wait for process and stderr thread to complete
    process.wait(timeout=timeout)
    stderr_thread.join(timeout=10)
    stderr_output = "".join(stderr_lines)

    LOG.debug(f"Command finished. returncode={process.returncode}")
    if stderr_output:
        LOG.debug(f"stderr: {stderr_output}")

    if process.returncode != 0:
        if state_lock_flag[0] or "remote state already locked" in stderr_output:
            raise TerraformStateLockedException(
                f"terraform command failed (state locked): {' '.join(cmd)}\n"
                f"stderr: {stderr_output}"
            )
        raise TerraformException(
            f"terraform command failed: {' '.join(cmd)}\n"
            f"stderr: {stderr_output}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_terraform.py::TestRunTerraformCommand`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd sunbeam-python
git add sunbeam/core/terraform.py tests/unit/sunbeam/core/test_terraform.py
git commit -m "feat: add _run_terraform_command() with JSON streaming

Uses subprocess.Popen for line-by-line stdout reading, stderr in
background thread, state lock detection from diagnostic JSON events
and stderr fallback."
```

---

## Task 6: Refactor apply(), destroy(), sync() to Use Streaming

**Files:**
- Modify: `sunbeam-python/sunbeam/core/terraform.py`
- Modify: `sunbeam-python/tests/unit/sunbeam/core/test_terraform.py`

- [ ] **Step 1: Refactor apply()**

Replace the body of `apply()` (lines 190-228) with:

```python
def apply(
    self,
    extra_args: list | None = None,
    reporter: ProgressReporter | None = None,
):
    """Terraform apply."""
    os_env = os.environ.copy()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    tf_log = str(self.path / f"terraform-apply-{timestamp}.log")
    os_env.update({"TF_LOG_PATH": tf_log})
    os_env.setdefault("TF_LOG", "INFO")
    if self.env:
        os_env.update(self.env)

    cmd = [self.terraform, "apply", "-json"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["-auto-approve", "-no-color"])
    if self.parallelism is not None:
        cmd.append(f"-parallelism={self.parallelism}")

    LOG.debug(f"Running terraform apply, cwd: {self.path}, tf log: {tf_log}")
    self._run_terraform_command(cmd=cmd, env=os_env, reporter=reporter)
```

- [ ] **Step 2: Refactor destroy()**

Replace the body of `destroy()` with:

```python
def destroy(self, reporter: ProgressReporter | None = None):
    """Terraform destroy."""
    os_env = os.environ.copy()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    tf_log = str(self.path / f"terraform-destroy-{timestamp}.log")
    os_env.update({"TF_LOG_PATH": tf_log})
    os_env.setdefault("TF_LOG", "INFO")
    if self.env:
        os_env.update(self.env)

    cmd = [
        self.terraform,
        "destroy",
        "-json",
        "-auto-approve",
        "-no-color",
        "-input=false",
    ]
    if self.parallelism is not None:
        cmd.append(f"-parallelism={self.parallelism}")

    LOG.debug(f"Running terraform destroy, cwd: {self.path}, tf log: {tf_log}")
    self._run_terraform_command(cmd=cmd, env=os_env, reporter=reporter)
```

**Important**: The existing `destroy()` uses `-no-color` and `-input=false` flags.
These MUST be preserved in the new implementation.

- [ ] **Step 3: Refactor sync()**

Replace the body of `sync()` with:

```python
def sync(self, reporter: ProgressReporter | None = None) -> None:
    """Sync the running state back to the Terraform state file."""
    os_env = os.environ.copy()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    tf_log = str(self.path / f"terraform-sync-{timestamp}.log")
    os_env.update({"TF_LOG_PATH": tf_log})
    os_env.setdefault("TF_LOG", "INFO")
    if self.env:
        os_env.update(self.env)

    cmd = [self.terraform, "apply", "-json", "-refresh-only", "-auto-approve"]

    LOG.debug(f"Running terraform sync, cwd: {self.path}, tf log: {tf_log}")
    self._run_terraform_command(cmd=cmd, env=os_env, reporter=reporter)
```

- [ ] **Step 4: Thread reporter through update_tfvars_and_apply_tf() and update_partial_tfvars_and_apply_tf()**

Add `reporter: ProgressReporter | None = None` parameter to both methods and pass it to `self.apply(...)`:

In `update_tfvars_and_apply_tf()` (line 451):
```python
def update_tfvars_and_apply_tf(
    self,
    client: Client,
    manifest: Manifest,
    tfvar_config: str | None = None,
    override_tfvars: dict | None = None,
    tf_apply_extra_args: list | None = None,
    reporter: ProgressReporter | None = None,
) -> None:
    # ... existing body unchanged ...
    self.apply(tf_apply_extra_args, reporter=reporter)
```

In `update_partial_tfvars_and_apply_tf()` (line 419):
```python
def update_partial_tfvars_and_apply_tf(
    self,
    client: Client,
    manifest: Manifest,
    charms: list[str],
    tfvar_config: str | None = None,
    tf_apply_extra_args: list | None = None,
    reporter: ProgressReporter | None = None,
) -> None:
    # ... existing body unchanged ...
    self.apply(tf_apply_extra_args, reporter=reporter)
```

- [ ] **Step 5: Run existing terraform tests to verify no regression**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/sunbeam/core/test_terraform.py`
Expected: All existing tests PASS (they mock `apply()` so the internal change is transparent)

- [ ] **Step 6: Commit**

```bash
cd sunbeam-python
git add sunbeam/core/terraform.py
git commit -m "feat: refactor apply/destroy/sync to use JSON streaming

All three methods now use _run_terraform_command() with -json flag
for structured output. Reporter parameter threaded through
update_tfvars_and_apply_tf() and update_partial_tfvars_and_apply_tf()."
```

---

## Task 7: Thread Reporter from Steps to TerraformHelper

**Files:**
- Modify: `sunbeam-python/sunbeam/core/steps.py` (DeployMachineApplicationStep, DestroyMachineApplicationStep)
- Modify: all files that call `tfhelper.update_tfvars_and_apply_tf()`, `tfhelper.apply()`, or `tfhelper.destroy()` directly

Steps that call tfhelper methods must now pass `context.reporter`:

**In sunbeam/core/steps.py:**
- `DeployMachineApplicationStep.run()`: change `self.tfhelper.update_tfvars_and_apply_tf(...)` to include `reporter=context.reporter`
- `DestroyMachineApplicationStep.run()`: same

**In sunbeam/steps/:**
- `microovn.py`: `update_tfvars_and_apply_tf(...)` → add `reporter=context.reporter`
- `openstack.py`: all `update_tfvars_and_apply_tf(...)` and `destroy(...)` calls → add `reporter=context.reporter`
- `hypervisor.py`: `update_tfvars_and_apply_tf(...)` → add `reporter=context.reporter`
- `sso.py`: all `apply(...)` calls → add `reporter=context.reporter`
- `configure.py` (commands): `apply(...)` call → add `reporter=context.reporter`
- `upgrades/inter_channel.py`: **Special case** — `run()` calls helper methods
  `pre_upgrade_tasks(status)`, `upgrade_tasks(status)`, `post_upgrade_tasks(status)`,
  and `upgrade_applications(status)` which internally call
  `update_partial_tfvars_and_apply_tf()` and `update_status_background()`. These
  helper methods must be updated to accept `context: StepContext` instead of
  `status: Status | None`, and thread `context.reporter` to the tfhelper calls and
  `context.status` to `update_status_background()` calls.

**In sunbeam/storage/:**
- `storage/steps.py`: `update_tfvars_and_apply_tf(...)` calls → add `reporter=context.reporter`

**In sunbeam/features/:**
- `baremetal/steps.py`: **Special case** — `run()` calls `_run()` which calls
  `_apply_tfvars()` which calls `update_tfvars_and_apply_tf()`. The reporter must be
  threaded through this chain: `run(context)` → `_run(context)` → `_apply_tfvars(reporter)` →
  `update_tfvars_and_apply_tf(..., reporter=reporter)`. Update `_run()` to accept
  `context` and `_apply_tfvars()` to accept `reporter`.
- `interface/v1/openstack.py`: `update_tfvars_and_apply_tf(...)` and `destroy(...)` calls → add `reporter=context.reporter`
- `instance_recovery/consul.py`: `update_tfvars_and_apply_tf(...)` and `destroy(...)` calls → add `reporter=context.reporter`
- `ldap/feature.py`: `apply(...)` calls → add `reporter=context.reporter`
- `pro/feature.py`: `update_tfvars_and_apply_tf(...)` calls → add `reporter=context.reporter`
- `observability/feature.py`: `update_tfvars_and_apply_tf(...)` and `destroy(...)` calls → add `reporter=context.reporter`
- `validation/feature.py`: `update_tfvars_and_apply_tf(...)` → add `reporter=context.reporter`

- [ ] **Step 1: Find all tfhelper call sites**

Run:
```bash
cd sunbeam-python
grep -rn "tfhelper\.\(update_tfvars_and_apply_tf\|update_partial_tfvars_and_apply_tf\|apply\|destroy\|sync\)(" sunbeam/
```

- [ ] **Step 2: Add reporter=context.reporter to each call**

For each call site, add the `reporter=context.reporter` keyword argument. The `context` variable is available because these calls are inside `run()` methods that now receive `StepContext`.

- [ ] **Step 3: Run full test suite**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/ -x`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd sunbeam-python
git add sunbeam/
git commit -m "feat: thread reporter from steps to TerraformHelper

All step run() methods now pass context.reporter to tfhelper
methods (update_tfvars_and_apply_tf, apply, destroy) enabling
real-time progress reporting during terraform operations."
```

---

## Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd sunbeam-python && uv run --frozen --isolated --extra=dev python -m pytest -vv tests/unit/`
Expected: All tests PASS

- [ ] **Step 2: Run linting**

Run: `cd sunbeam-python && tox -e pep8`
Expected: PASS

- [ ] **Step 3: Run type checking**

Run: `cd sunbeam-python && tox -e mypy`
Expected: PASS (or pre-existing failures only)

- [ ] **Step 4: Review the full diff**

Run: `git diff main --stat` to review the scope of changes.
Verify:
- `sunbeam/core/progress.py` exists with all reporter classes
- `BaseStep.run()` and `is_skip()` take `StepContext`
- `run_plan()` constructs reporters and `StepContext`
- `TerraformHelper.apply()/destroy()/sync()` use `_run_terraform_command()` with `-json`
- All step overrides pass `context.reporter` to tfhelper methods
- All tests pass `StepContext` to `step.run()`/`step.is_skip()`
