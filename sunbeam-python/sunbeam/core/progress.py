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

    def __enter__(self) -> "CompositeProgressReporter":
        """Enter the context manager for all reporters."""
        for reporter in self.reporters:
            if hasattr(reporter, "__enter__"):
                reporter.__enter__()  # type: ignore[union-attr]
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the context manager for all reporters."""
        for reporter in self.reporters:
            if hasattr(reporter, "__exit__"):
                reporter.__exit__(*args)  # type: ignore[union-attr]

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event to all registered reporters."""
        for reporter in self.reporters:
            reporter.report(event)


class RichProgressReporter:
    """Displays a rolling window of events below the Rich spinner.

    The spinner + step name stays on the first line, with dim event lines
    rendered below it. Uses only the public Status.update() API.

    Use as a context manager to automatically clear the event lines when done::

        with RichProgressReporter(status, base_message) as reporter:
            reporter.report(event)
        # event lines are cleared, spinner shows only the base message
    """

    def __init__(self, status: Status, base_message: str, max_lines: int = 3):
        self._status = status
        self._base_message = base_message
        self._recent_events: deque[str] = deque(maxlen=max_lines)

    def __enter__(self) -> "RichProgressReporter":
        """Enter the context manager."""
        return self

    def __exit__(self, *args: object) -> None:
        """Reset the status to just the base message."""
        self._recent_events.clear()
        self._status.update(self._base_message)

    def report(self, event: ProgressEvent) -> None:
        """Report a progress event by updating the Rich status display."""
        self._recent_events.append(event.message)
        lines = [Text(f"  {msg}", style="dim") for msg in self._recent_events]
        self._status.update(Group(Text(self._base_message), *lines))
