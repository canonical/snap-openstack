# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Steps for ``sunbeam restore``."""

import logging

from sunbeam.core.common import BaseStep, Result, ResultType, StepContext
from sunbeam.core.juju import (
    JujuException,
    JujuHelper,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.steps.backup import BackupTarget

LOG = logging.getLogger(__name__)

DEFAULT_SCALE_TIMEOUT = 1800
DEFAULT_RESTORE_TIMEOUT = 1800


class PauseControlPlaneStep(BaseStep):
    """Pause OpenStack control-plane API services before a MySQL restore."""

    def __init__(self, jhelper: JujuHelper, model: str = OPENSTACK_MODEL):
        super().__init__("Pause control-plane", "Pausing control-plane services")
        self.jhelper = jhelper
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Fail cleanly: pause is not yet supported by the charms."""
        return Result(ResultType.FAILED, "Unimplemented")


class ResumeControlPlaneStep(BaseStep):
    """Resume OpenStack control-plane API services after a MySQL restore."""

    def __init__(self, jhelper: JujuHelper, model: str = OPENSTACK_MODEL):
        super().__init__("Resume control-plane", "Resuming control-plane services")
        self.jhelper = jhelper
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Fail cleanly: resume is not yet supported by the charms."""
        return Result(ResultType.FAILED, "Unimplemented")


class ScaleMySQLStep(BaseStep):
    """Scale a MySQL application to a target number of units."""

    def __init__(
        self,
        jhelper: JujuHelper,
        application: str,
        scale: int,
        timeout: int = DEFAULT_SCALE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ) -> None:
        super().__init__("Scale MySQL", f"Scaling {application} to {scale} unit(s)")
        self.jhelper = jhelper
        self.application = application
        self.scale = scale
        self.timeout = timeout
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Scale the application and wait for it to settle."""
        try:
            self.jhelper.scale_application(self.model, self.application, self.scale)
            self.jhelper.wait_until_active(
                self.model, apps=[self.application], timeout=self.timeout
            )
        except (JujuException, JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class RestoreMySQLStep(BaseStep):
    """Restore a MySQL application from a backup."""

    def __init__(
        self,
        jhelper: JujuHelper,
        target: BackupTarget,
        restore_to_time: str | None = None,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore MySQL", f"Restoring {target.app}")
        self.jhelper = jhelper
        self.target = target
        self.restore_to_time = restore_to_time
        self.model = model
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Fail cleanly: restore is gated on control-plane pause support."""
        return Result(ResultType.FAILED, "Unimplemented")


class RestoreVaultStep(BaseStep):
    """Restore a Vault application from a backup.

    Skeleton: surfaces the unseal-keys/root-token prerequisite and does not
    perform the restore in this iteration.
    """

    def __init__(
        self,
        jhelper: JujuHelper,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore Vault", "Restoring Vault")
        self.jhelper = jhelper
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Fail cleanly, surfacing the Vault restore prerequisite."""
        return Result(ResultType.FAILED, "Unimplemented")
