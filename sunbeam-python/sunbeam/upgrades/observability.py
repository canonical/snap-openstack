# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Upgrade observability: state transitions logged to the standard debug log.

Every state mutation in the coordinator emits a structured log line at
DEBUG level via the standard sunbeam logger. No separate upgrade.log
file — the debug log at
``$HOME/snap/openstack/common/logs/sunbeam-<timestamp>.log`` is the
audit trail.
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)


class UpgradeLogger:
    """Logs state transitions and lock events via LOG.debug."""

    def log_state_change(
        self,
        component: str,
        component_name: str,
        action: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a state transition at DEBUG level."""
        LOG.debug(
            "upgrade state: component=%s name=%s action=%s status=%s%s%s",
            component,
            component_name,
            action,
            status,
            f" error_code={error_code}" if error_code else "",
            f" error={error_message}" if error_message else "",
        )

    def log_lock_event(
        self,
        event: str,
        token: int,
        holder_id: str | None = None,
    ) -> None:
        """Log a lock event at DEBUG level."""
        LOG.debug(
            "upgrade lock: event=%s token=%d%s",
            event,
            token,
            f" holder={holder_id}" if holder_id else "",
        )

    def log_command(
        self,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        """Log a CLI command invocation at DEBUG level."""
        LOG.debug(
            "upgrade command: %s%s",
            command,
            f" args={args}" if args else "",
        )
