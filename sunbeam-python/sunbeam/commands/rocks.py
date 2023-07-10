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
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import CONTROLLER_MODEL

LOG = logging.getLogger(__name__)
snap = Snap()

GHCR_OPENSTACK_SNAPS = "ghcr.io/openstack-snaps/{name}:{tag}"
GHCR_CANONICAL = "ghcr.io/canonical/{name}@{digest}"

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
        "template": GHCR_OPENSTACK_SNAPS,
    },
    {
        "rocks": ["ovn-sb-db-server", "ovn-nb-db-server", "ovn-northd"],
        "tag": "23.03",
        "template": GHCR_OPENSTACK_SNAPS,
    },
    {"rocks": ["rabbitmq"], "tag": "3.9.13", "template": GHCR_OPENSTACK_SNAPS},
    {
        "rocks": ["charmed-mysql"],
        "digest": "sha256:c2dd359ddcf2cbf598da09769ac54faecc9b5f4b6ca804d0b8aa0055c25a9c48",  # noqa
        "template": GHCR_CANONICAL,
    },
    {
        "rocks": ["charmed-mysql"],
        "digest": "sha256:017605f168fcc569d10372bb74b29ef9041256bd066013dec39e9ceee8c88539",  # noqa
        "template": GHCR_CANONICAL,
    },
]
SSH = ["juju", "ssh", "-m", CONTROLLER_MODEL]
MICROK8S = ["sudo", "microk8s"]
REGISTRIES = ("docker.io", "quay.io", "ghcr.io", "k8s.gcr.io")
HOSTS_TEMPLATE = """
server = "https://{registry}"

[host."http://{cache_address}"]
    capabilities = ["pull", "resolve"]
"""
HOSTS_PATH = "/var/snap/microk8s/current/args/certs.d/{registry}/hosts.toml"


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


def pull_image_by_digest(machine_id, name, digest, template):
    image = template.format(name=name, digest=digest)
    _pull_image(machine_id, image)


def pull_image_by_tag(machine_id, name, tag, template):
    image = template.format(name=name, tag=tag)
    _pull_image(machine_id, image)


def _pull_image(machine_id, image):
    https_proxy = snap.config.get("proxy.https")
    cmd = SSH + [machine_id]
    if https_proxy:
        cmd.append(f"https_proxy={https_proxy}")
    cmd.extend(MICROK8S)
    cmd.extend(
        [
            "ctr",
            "images",
            "pull",
            "--hosts-dir",
            os.path.dirname(os.path.dirname(HOSTS_PATH)),
            image,
        ]
    )
    run(cmd)


def is_host_templated(machine_id: str, registry: str, cache_address: str):
    """Check if the hosts.toml file is templated."""
    hosts_path = HOSTS_PATH.format(registry=registry)
    cmd = SSH + [machine_id, "test", "-f", hosts_path]
    process = run(cmd, check=False)
    if process.returncode > 0:
        return False

    cmd = SSH + [
        machine_id,
        "grep",
        f'[host."http://{cache_address}"]',
        hosts_path,
    ]
    process = run(cmd, check=False)
    if process.returncode > 0:
        return False
    return True


def template_hosts(machine_id: str, registry: str, cache_address: str, home_dir: Path):
    """Template the hosts.toml file for the pull through cache."""
    hosts_path = HOSTS_PATH.format(registry=registry)
    cmd = SSH + [
        machine_id,
        "mkdir",
        "-p",
        os.path.dirname(hosts_path),
    ]
    run(cmd)
    cmd = SSH + [
        machine_id,
        "sudo",
        "chown",
        "root:snap_microk8s",
        os.path.dirname(hosts_path),
    ]
    run(cmd)
    tmp_dir = home_dir / ".config/openstack/"
    with tempfile.NamedTemporaryFile("w", prefix=registry, dir=tmp_dir) as fd:
        fd.write(HOSTS_TEMPLATE.format(registry=registry, cache_address=cache_address))
        fd.flush()
        cmd = SSH + [machine_id, "cp", fd.name, hosts_path]
        run(cmd)
    cmd = SSH + [
        machine_id,
        "sudo",
        "chown",
        "root:snap_microk8s",
        hosts_path,
    ]
    run(cmd)
    cmd = SSH + [machine_id, "sudo", "chmod", "0660", hosts_path]
    run(cmd)


class ConfigurePullThroughCacheStep(BaseStep):
    """Configure pull through cache"""

    def __init__(self, name: str):
        super().__init__("Configure pull through cache", "Configure pull through cache")
        self.name = name
        self.snap = Snap()
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

        for registry in REGISTRIES:
            cache_address = self.snap.config.get("cache." + registry.replace(".", "-"))
            if cache_address and not is_host_templated(
                self.machine_id, registry, cache_address
            ):
                return Result(ResultType.COMPLETED)
        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Runs the step.

        :param status: Rich Status object to update with progress
        :return: ResultType.COMPLETED or ResultType.FAILED
        """

        for registry in REGISTRIES:
            cache_address = self.snap.config.get("cache." + registry.replace(".", "-"))
            if not cache_address:
                continue
            template_hosts(
                self.machine_id, registry, cache_address, self.snap.paths.real_home
            )

        run(SSH + [self.machine_id] + MICROK8S + ["stop"])
        run(SSH + [self.machine_id] + MICROK8S + ["start"])
        return Result(ResultType.COMPLETED)


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
                    if "digest" in group:
                        pull_image_by_digest(
                            machine_id, image, group["digest"], group["template"]
                        )
                    if "tag" in group:
                        pull_image_by_tag(
                            machine_id, image, group["tag"], group["template"]
                        )
                except Exception:
                    LOG.debug(
                        f"Failed to pull image '{image}', skipping it",
                        exc_info=True,
                    )

        return Result(ResultType.COMPLETED)
