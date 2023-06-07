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
import subprocess
from typing import Optional

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import CONTROLLER_MODEL

LOG = logging.getLogger(__name__)

GHCR = "ghcr.io/openstack-snaps/{name}:{tag}"

ROCK_GROUPS = [
    {
        "rocks": [
            "keystone",
            "glance-api",
            "nova-api",
            "nova-scheduler",
            "nova-conductor",
            "horizon",
            "cinder-api",
            "cinder-scheduler",
            "cinder-volume",
            "neutron-server",
            "placement-api",
        ],
        "tag": "2023.1",
    },
    {
        "rocks": ["ovn-sb-db-server", "ovn-nb-db-server", "ovn-northd"],
        "tag": "23.03",
    },
    {"rocks": ["rabbitmq"], "tag": "3.9.13"},
]
SSH = ["juju", "ssh", "-m", CONTROLLER_MODEL]
MICROK8S = ["sudo", "microk8s"]


def run(cmd, check=True):
    LOG.debug(f'Running command {" ".join(cmd)}')
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )
    LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")
    return process


def run_shell(cmd, check=True):
    """Separate function to run a shell command.

    This is to make sure no call to subprocess run is made with shell=True
    with data from the outside world.
    """
    LOG.debug(f'Running command {" ".join(cmd)}')
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        shell=True,
    )  # nosec
    LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")
    return process


def pull_image(machine_id, name, tag):
    image = GHCR.format(name=name, tag=tag)

    cmd = SSH + [machine_id] + MICROK8S + ["ctr", "images", "pull", image]
    run(cmd)


class ConfigureKubeletOptionsStep(BaseStep):
    """Configure kubelet options"""

    def __init__(self, name: str):
        super().__init__("Configure kubelet options", "Configure kubelet options")
        self.name = name
        self.machine_id = ""

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))
        cmd = SSH + [
            self.machine_id,
            "grep",
            "serialize-image-pulls",
            "/var/snap/microk8s/current/args/kubelet",
        ]
        process = run(cmd, check=False)
        # This will actually return 2 when the file does not exist
        # which is the case during the first run
        # We would neeed to split the plans further to handle things cleanly
        if process.returncode != 0:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Runs the step.

        :param status: Rich Status object to update with progress
        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        cmd = SSH + [
            self.machine_id,
            (
                "'echo --serialize-image-pulls=false "
                ">> /var/snap/microk8s/current/args/kubelet'"
            ),
        ]
        # Need as a single string for file redirection
        run_shell(" ".join(cmd))
        run(SSH + [self.machine_id] + MICROK8S + ["stop"])
        run(SSH + [self.machine_id] + MICROK8S + ["start"])
        return Result(ResultType.COMPLETED)


class PreseedRocksStep(BaseStep):
    """Preseed ROCKS into Microk8s"""

    def __init__(self, name: str):
        super().__init__("Preseed ROCKs", "Preseed ROCKs into Microk8s")
        self.name = name

    def run(self, status: Optional[Status] = None) -> Result:
        """Runs the step.

        :param status: Rich Status object to update with progress
        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))

        for group in ROCK_GROUPS:
            for image in group["rocks"]:
                try:
                    pull_image(machine_id, image, group["tag"])
                except Exception:
                    LOG.debug(
                        f"Failed to pull image '{image}:{group['tag']}', skipping it",
                        exc_info=True,
                    )

        return Result(ResultType.COMPLETED)
