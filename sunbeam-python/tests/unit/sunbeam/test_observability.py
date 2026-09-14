# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the upgrade observability logger."""

import logging

import pytest

from sunbeam.upgrades.observability import UpgradeLogger


@pytest.fixture
def logger() -> UpgradeLogger:
    return UpgradeLogger()


@pytest.fixture
def debug_log(caplog):
    logger_name = "sunbeam.upgrades.observability"
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        yield caplog


class TestLogStateChange:
    def test_logs_state_change(self, logger, debug_log):
        logger.log_state_change(
            "phase", "control_plane", "phase_started", "in_progress"
        )
        record = debug_log.records[0]
        assert record.levelno == logging.DEBUG
        assert "component=phase" in record.message
        assert "name=control_plane" in record.message
        assert "action=phase_started" in record.message
        assert "status=in_progress" in record.message

    def test_includes_error_fields(self, logger, debug_log):
        logger.log_state_change(
            "phase",
            "dataplane",
            "phase_failed",
            "failed",
            error_code="DATAPLANE_REGISTRATION_TIMEOUT",
            error_message="nova-compute did not re-register",
        )
        message = debug_log.records[0].message
        assert "error_code=DATAPLANE_REGISTRATION_TIMEOUT" in message
        assert "nova-compute did not re-register" in message

    def test_omits_error_fields_when_none(self, logger, debug_log):
        logger.log_state_change("phase", "preflight", "phase_completed", "completed")
        message = debug_log.records[0].message
        assert "error_code" not in message
        assert "error=" not in message


class TestLogLockEvent:
    def test_acquired(self, logger, debug_log):
        logger.log_lock_event("acquired", 42, holder_id="host-pid")
        message = debug_log.records[0].message
        assert "event=acquired" in message
        assert "token=42" in message
        assert "holder=host-pid" in message

    def test_released_without_holder(self, logger, debug_log):
        logger.log_lock_event("released", 42)
        message = debug_log.records[0].message
        assert "event=released" in message
        assert "holder" not in message


class TestLogCommand:
    def test_command_invocation(self, logger, debug_log):
        logger.log_command("control-plane --auto", args={"group": "all"})
        message = debug_log.records[0].message
        assert "control-plane --auto" in message
        assert "group" in message

    def test_command_without_args(self, logger, debug_log):
        logger.log_command("finalize")
        message = debug_log.records[0].message
        assert "finalize" in message
        assert "args=" not in message
