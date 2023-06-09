# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
from typing import Optional

from rich.status import Status

from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    JujuException,
    JujuHelper,
    ModelNotFoundException,
    run_sync,
)

MAX_CONNECTIONS = 500

LOG = logging.getLogger(__name__)


def get_mysqls(jhelper: JujuHelper) -> list[str]:
    try:
        apps = run_sync(jhelper.get_application_names(OPENSTACK_MODEL))
    except JujuException as e:
        LOG.debug("Failed to get application names", exc_info=True)
        raise e
    mysqls = list(filter(lambda app: app.endswith("mysql"), apps))
    if len(mysqls) == 0:
        raise JujuException("No MySQL applications found")
    return mysqls


class ConfigureMySQLStep(BaseStep):
    """Post Deployment step to configure MySQL."""

    def __init__(self, jhelper: JujuHelper):
        super().__init__("Configure MySQL", "Configure MySQL")
        self.jhelper = jhelper

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """

        try:
            run_sync(self.jhelper.get_model(OPENSTACK_MODEL))
        except ModelNotFoundException:
            return Result(ResultType.FAILED, "Openstack model must be deployed.")

        try:
            get_mysqls(self.jhelper)
        except JujuException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Runs the step.

        :return: ResultType.COMPLETED or ResultType.FAILED
        """

        try:
            mysqls = get_mysqls(self.jhelper)
        except JujuException as e:
            return Result(ResultType.FAILED, str(e))

        username = "serverconfig"
        for mysql in mysqls:
            LOG.debug(f"Configuring {mysql}")
            try:
                leader = run_sync(self.jhelper.get_leader_unit(mysql, OPENSTACK_MODEL))
            except JujuException as e:
                LOG.debug(f"Failed to get {mysql} leader", exc_info=True)
                return Result(ResultType.FAILED, str(e))
            try:
                result = run_sync(
                    self.jhelper.run_action(
                        leader, OPENSTACK_MODEL, "get-password", {"username": username}
                    )
                )
            except JujuException as e:
                LOG.debug(
                    f"Failed to get {leader} password for {username}", exc_info=True
                )
                return Result(ResultType.FAILED, str(e))
            password = result["password"]
            cmd = " ".join(
                [
                    "mysql",
                    "-u",
                    username,
                    # password cannot be separated from -p
                    f"-p{password}",
                    "-e",
                    # this is executed in shell script, quotes needed
                    f"'set global max_connections = {MAX_CONNECTIONS}'",
                ]
            )
            try:
                run_sync(
                    self.jhelper.run_cmd_on_unit_payload(
                        leader, OPENSTACK_MODEL, cmd, "mysql"
                    )
                )
            except JujuException as e:
                LOG.debug(f"Failed to set max_connections on {mysql}", exc_info=True)
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
