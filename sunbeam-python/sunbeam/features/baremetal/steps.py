# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

from rich.console import Console
from rich.status import Status

from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants

LOG = logging.getLogger(__name__)
console = Console()


class RunSetTempUrlSecretStep(BaseStep, JujuStepHelper):
    """Run the set-temp-url-secret action on the ironic-conductor."""

    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Run the set-temp-url-secret action on ironic-conductor",
            "Running the set-temp-url-secret action on ironic-conductor",
        )
        self.jhelper = jhelper
        self.deployment = deployment
        self.model = OPENSTACK_MODEL

    def run(self, status: Status | None = None) -> Result:
        """Run the set-temp-url-secret action on ironic-conductor."""
        try:
            unit = self.jhelper.get_leader_unit(
                constants.IRONIC_CONDUCTOR_APP,
                self.model,
            )
            self.jhelper.run_action(
                unit,
                self.model,
                "set-temp-url-secret",
            )
        except (ActionFailedException, LeaderNotFoundException) as e:
            LOG.error(
                "Error running the set-temp-url-secret action on ironic-conductor: %s",
                e,
            )
            return Result(ResultType.FAILED, str(e))

        apps = [constants.IRONIC_CONDUCTOR_APP]
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=constants.IRONIC_APP_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)
