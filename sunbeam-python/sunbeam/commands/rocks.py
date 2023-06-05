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

import requests
from rich.status import Status

from sunbeam.jobs.common import BaseStep, Result, ResultType


LOG = logging.getLogger(__name__)

CHARMHUB = "https://api.charmhub.io/v2/charms"
CHARM_URL = CHARMHUB + "/info/{name}?fields=default-release.resources&channel={channel}"
GHCR = "ghcr.io/openstack-snaps/{name}:{tag}"

CHARM_GROUPS = [
    {
        "charms": [
            "keystone-k8s",
            "glance-k8s",
            "nova-k8s",
            "horizon-k8s",
            "cinder-k8s",
            "cinder-ceph-k8s",
            "neutron-k8s",
            "placement-k8s",
        ],
        "channel": "2023.1/edge",
        "tag": "2023.1",
    },
    {
        "charms": [
            "ovn-central-k8s",
            "ovn-relay-k8s",
        ],
        "channel": "23.03/edge",
        "tag": "23.03",
    },
    {"charms": ["rabbitmq-k8s"], "channel": "3.9/beta", "tag": "3.9.13"},
]
SSH = [
    "juju",
    "ssh",
    "0",
]
MICROK8S = SSH + ["sudo", "microk8s"]


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


def fetch_image_names(name, channel):
    url = CHARM_URL.format(name=name, channel=channel)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    resources = data["default-release"].get("resources", [])
    images = []
    for resource in resources:
        if resource["type"] != "oci-image":
            continue
        images.append(resource["name"])
    return images


def pull_image(name, tag):
    image = GHCR.format(name=name, tag=tag)

    cmd = MICROK8S + ["ctr", "images", "pull", image]
    run(cmd)


class ConfigureKubeletOptions(BaseStep):
    """Configure kubelet options"""

    def __init__(self):
        super().__init__("Configure kubelet options", "Configure kubelet options")

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        cmd = SSH + [
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
            (
                "'echo --serialize-image-pulls=false "
                ">> /var/snap/microk8s/current/args/kubelet'"
            ),
        ]
        # Need as a single string for file redirection
        run_shell(" ".join(cmd))
        run(MICROK8S + ["stop"])
        run(MICROK8S + ["start"])
        return Result(ResultType.COMPLETED)


class PreseedRocksStep(BaseStep):
    """Preseed ROCKS into Microk8s"""

    def __init__(self):
        super().__init__("Preseed ROCKs", "Preseed ROCKs into Microk8s")

    def run(self, status: Optional[Status] = None) -> Result:
        """Runs the step.

        :param status: Rich Status object to update with progress
        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        for group in CHARM_GROUPS:
            for charm in group["charms"]:
                images = fetch_image_names(charm, group["channel"])
                for image in images:
                    pull_image(image[:-6], group["tag"])

        return Result(ResultType.COMPLETED)
