# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from rich.console import Group
from rich.status import Status
from rich.text import Text

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

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event."""
        ...


class NoOpReporter:
    """Reporter that does nothing. Used in tests or when no reporting is needed."""

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event (no-op)."""


class LoggingProgressReporter:
    """Logs each event as structured JSON at DEBUG level."""

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event by logging it as JSON."""
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
        """Report a progress event to all registered reporters."""
        for reporter in self.reporters:
            reporter.report(event)


class RichProgressReporter:
    """Displays a 3-line rolling window of events above the Rich spinner."""

    def __init__(self, status: Status, base_message: str, max_lines: int = 3):
        self._status = status
        self._base_message = base_message
        self._recent_events: deque[str] = deque(maxlen=max_lines)

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event by updating the Rich status display."""
        self._recent_events.append(event.message)
        lines = [Text(f"  {msg}", style="dim") for msg in self._recent_events]
        # Update the Live display directly so event lines render ABOVE the
        # spinner.  Status.update() would place the spinner on the first line
        # of the Group; by setting _live's renderable to Group(lines, status)
        # the Status renderable (spinner + text) appears on the last line.
        self._status._live.update(Group(*lines, self._status))
