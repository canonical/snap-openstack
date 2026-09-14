# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Error code catalog for the upgrade engine.

Every phase handler that sets ``last_error`` on a failed phase/group/node/step
uses a code from this catalog. The ``status`` command surfaces the
code + message to the operator so they can act without a separate command.

Codes follow the convention ``<COMPONENT>_<FAILURE>``:
- LOCK_* — advisory lock failures
- METADATA_* — metadata loading/validation failures
- PREFLIGHT_* — preflight check failures
- CONTROL_PLANE_* — control-plane phase failures
- DATAPLANE_* — data-plane phase failures
- STORAGE_* — storage phase failures
- FINALIZE_* — finalize phase failures

Adding a new code: add it here. The catalog is closed — tests assert on
codes, status renders stable messages per code.
"""

from __future__ import annotations

import enum


class UpgradeErrorCode(str, enum.Enum):
    """Closed catalog of upgrade error codes."""

    # Lock-related
    LOCK_HELD = "LOCK_HELD"
    FENCING_TOKEN_MISMATCH = "FENCING_TOKEN_MISMATCH"

    # Metadata-related
    METADATA_INCOMPAT = "METADATA_INCOMPAT"
    METADATA_MISSING = "METADATA_MISSING"
    METADATA_INVALID = "METADATA_INVALID"

    # Preflight
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    PREFLIGHT_BACKUP_FAILED = "PREFLIGHT_BACKUP_FAILED"
    PREFLIGHT_RESTORE_FAILED = "PREFLIGHT_RESTORE_FAILED"
    PREFLIGHT_CAPACITY = "PREFLIGHT_CAPACITY"
    PREFLIGHT_HEALTH_CHECK = "PREFLIGHT_HEALTH_CHECK"

    # Control-plane phase
    CONTROL_PLANE_CONVERGENCE_TIMEOUT = "CONTROL_PLANE_CONVERGENCE_TIMEOUT"
    CONTROL_PLANE_APPLY_FAILED = "CONTROL_PLANE_APPLY_FAILED"
    CONTROL_PLANE_ACTION_FAILED = "CONTROL_PLANE_ACTION_FAILED"

    # Data-plane phase
    DATAPLANE_REGISTRATION_TIMEOUT = "DATAPLANE_REGISTRATION_TIMEOUT"
    DATAPLANE_REFRESH_FAILED = "DATAPLANE_REFRESH_FAILED"
    DATAPLANE_VM_CHECK_FAILED = "DATAPLANE_VM_CHECK_FAILED"

    # Storage phase
    STORAGE_REGISTRATION_TIMEOUT = "STORAGE_REGISTRATION_TIMEOUT"
    STORAGE_REFRESH_FAILED = "STORAGE_REFRESH_FAILED"
    STORAGE_BACKEND_UNSUPPORTED = "STORAGE_BACKEND_UNSUPPORTED"

    # Finalize phase
    FINALIZE_MIGRATION_FAILED = "FINALIZE_MIGRATION_FAILED"
    FINALIZE_VALIDATION_FAILED = "FINALIZE_VALIDATION_FAILED"

    # Cross-cutting
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    HOP_INVALID_TRANSITION = "HOP_INVALID_TRANSITION"


# Human-readable messages for each code. The status command uses
# these to render operator-facing output. Keep them actionable — tell the
# operator what to do, not just what went wrong.
ERROR_MESSAGES: dict[UpgradeErrorCode, str] = {
    UpgradeErrorCode.LOCK_HELD: (
        "Another upgrade operation is in progress. "
        "Complete or abandon the current upgrade before starting a new one."
    ),
    UpgradeErrorCode.FENCING_TOKEN_MISMATCH: (
        "The upgrade lock was acquired by another process after this one's "
        "lock expired. Re-run the command to acquire a fresh lock."
    ),
    UpgradeErrorCode.METADATA_INCOMPAT: (
        "The upgrade metadata is incompatible with this snap version. "
        "Refresh the snap to the target release before running upgrade."
    ),
    UpgradeErrorCode.METADATA_MISSING: (
        "Upgrade metadata not found for the target release. "
        "Ensure the snap is refreshed to the target release."
    ),
    UpgradeErrorCode.METADATA_INVALID: (
        "Upgrade metadata failed validation. Check the metadata file "
        "for missing or invalid fields."
    ),
    UpgradeErrorCode.PREFLIGHT_FAILED: (
        "Preflight checks failed. Review the output for specific failures "
        "and resolve them before retrying."
    ),
    UpgradeErrorCode.PREFLIGHT_BACKUP_FAILED: (
        "Backup creation failed. Ensure sufficient disk space and that "
        "Juju, MySQL, and clusterd are reachable."
    ),
    UpgradeErrorCode.PREFLIGHT_RESTORE_FAILED: (
        "Backup restore failed. The cluster may be in an inconsistent "
        "state. Contact support with the backup artifacts."
    ),
    UpgradeErrorCode.PREFLIGHT_CAPACITY: (
        "Insufficient compute capacity for a safe upgrade. Migrate VMs "
        "to free up nodes, or override with --capacity-policy-override."
    ),
    UpgradeErrorCode.PREFLIGHT_HEALTH_CHECK: (
        "Cluster health check failed. Resolve the reported issues "
        "before retrying the upgrade."
    ),
    UpgradeErrorCode.CONTROL_PLANE_CONVERGENCE_TIMEOUT: (
        "Control-plane group did not converge within the timeout. "
        "Check juju status for stuck units and retry with --retry-group."
    ),
    UpgradeErrorCode.CONTROL_PLANE_APPLY_FAILED: (
        "Terraform apply failed for a control-plane group. "
        "Check terraform output and retry with --retry-group."
    ),
    UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED: (
        "A pre-upgrade or post-upgrade action failed. "
        "Check the action output and retry the group."
    ),
    UpgradeErrorCode.DATAPLANE_REGISTRATION_TIMEOUT: (
        "Nova-compute or cinder-volume did not re-register within the "
        "timeout. Check the service status and retry with --retry-node."
    ),
    UpgradeErrorCode.DATAPLANE_REFRESH_FAILED: (
        "Charm or snap refresh failed on a compute node. "
        "Check juju status and retry with --retry-node, or rollback "
        "with --rollback-node."
    ),
    UpgradeErrorCode.DATAPLANE_VM_CHECK_FAILED: (
        "VMs are still running on the compute node. Migrate or stop "
        "them before retrying with --retry-node."
    ),
    UpgradeErrorCode.STORAGE_REGISTRATION_TIMEOUT: (
        "Cinder-volume did not re-register within the timeout. "
        "Check the service status and retry with --retry-node."
    ),
    UpgradeErrorCode.STORAGE_REFRESH_FAILED: (
        "Snap refresh failed on a storage node. Check juju status and "
        "retry with --retry-node."
    ),
    UpgradeErrorCode.STORAGE_BACKEND_UNSUPPORTED: (
        "Non-Ceph storage backend detected. Only Ceph-backed "
        "cinder-volume is supported for upgrades."
    ),
    UpgradeErrorCode.FINALIZE_MIGRATION_FAILED: (
        "Online data migration failed. Check the migration output and re-run finalize."
    ),
    UpgradeErrorCode.FINALIZE_VALIDATION_FAILED: (
        "End-state validation failed. Not all services are healthy. "
        "Check juju status and resolve issues before re-running finalize."
    ),
    UpgradeErrorCode.ROLLBACK_FAILED: (
        "Node rollback failed. The node may be in an inconsistent "
        "state. Check juju status and consider manual recovery."
    ),
    UpgradeErrorCode.HOP_INVALID_TRANSITION: (
        "Invalid state transition attempted. This indicates a bug "
        "in the upgrade coordinator. Report this issue."
    ),
}


def get_error_message(code: str | UpgradeErrorCode) -> str:
    """Return the human-readable message for an error code.

    Falls back to the code itself if no message is defined.
    """
    if isinstance(code, str):
        try:
            code = UpgradeErrorCode(code)
        except ValueError:
            return code
    return ERROR_MESSAGES.get(code, code.value)
