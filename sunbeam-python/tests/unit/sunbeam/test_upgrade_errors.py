# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the upgrade error code catalog (G9)."""

from sunbeam.upgrades.errors import (
    ERROR_MESSAGES,
    UpgradeErrorCode,
    get_error_message,
)


class TestErrorCodes:
    def test_all_codes_have_messages(self):
        for code in UpgradeErrorCode:
            assert code in ERROR_MESSAGES, f"{code} has no human-readable message"

    def test_codes_follow_naming_convention(self):
        for code in UpgradeErrorCode:
            # Each code should have at least one underscore (COMPONENT_FAILURE)
            assert "_" in code.value, f"{code} doesn't follow naming convention"

    def test_lock_codes(self):
        assert UpgradeErrorCode.LOCK_HELD.value == "LOCK_HELD"
        assert UpgradeErrorCode.FENCING_TOKEN_MISMATCH.value == "FENCING_TOKEN_MISMATCH"

    def test_metadata_codes(self):
        assert UpgradeErrorCode.METADATA_INCOMPAT.value == "METADATA_INCOMPAT"
        assert UpgradeErrorCode.METADATA_MISSING.value == "METADATA_MISSING"

    def test_preflight_codes(self):
        assert UpgradeErrorCode.PREFLIGHT_FAILED.value == "PREFLIGHT_FAILED"
        assert (
            UpgradeErrorCode.PREFLIGHT_BACKUP_FAILED.value == "PREFLIGHT_BACKUP_FAILED"
        )

    def test_control_plane_codes(self):
        assert (
            UpgradeErrorCode.CONTROL_PLANE_CONVERGENCE_TIMEOUT.value
            == "CONTROL_PLANE_CONVERGENCE_TIMEOUT"
        )

    def test_dataplane_codes(self):
        assert (
            UpgradeErrorCode.DATAPLANE_REGISTRATION_TIMEOUT.value
            == "DATAPLANE_REGISTRATION_TIMEOUT"
        )

    def test_storage_codes(self):
        assert (
            UpgradeErrorCode.STORAGE_BACKEND_UNSUPPORTED.value
            == "STORAGE_BACKEND_UNSUPPORTED"
        )

    def test_finalize_codes(self):
        assert (
            UpgradeErrorCode.FINALIZE_MIGRATION_FAILED.value
            == "FINALIZE_MIGRATION_FAILED"
        )


class TestGetErrorMessage:
    def test_returns_message_for_known_code(self):
        msg = get_error_message(UpgradeErrorCode.LOCK_HELD)
        assert "Another upgrade operation" in msg

    def test_accepts_string_code(self):
        msg = get_error_message("LOCK_HELD")
        assert "Another upgrade operation" in msg

    def test_returns_code_for_unknown_string(self):
        msg = get_error_message("UNKNOWN_CODE")
        assert msg == "UNKNOWN_CODE"

    def test_messages_are_actionable(self):
        """Every message should tell the operator what to do."""
        for code, msg in ERROR_MESSAGES.items():
            # Not a hard grammar check — just ensure messages aren't empty
            # and have reasonable length
            assert len(msg) > 20, f"{code} message is too short: {msg}"
            assert len(msg) < 300, f"{code} message is too long: {msg}"


class TestErrorCodesUnique:
    def test_all_values_unique(self):
        values = [code.value for code in UpgradeErrorCode]
        assert len(values) == len(set(values)), "Duplicate error code values"
