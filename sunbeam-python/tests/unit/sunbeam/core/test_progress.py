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
    RichProgressReporter,
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


class TestRichProgressReporter:
    def test_single_event_updates_live(self):
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
        status._live.update.assert_called_once()

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
        assert status._live.update.call_count == 5
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
        status._live.update.assert_called_once()
