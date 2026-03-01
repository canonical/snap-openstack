# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Abstract interface for Ceph storage providers.

This module defines the contract between the Sunbeam core orchestration
code and the storage infrastructure that provides Ceph services.

It also provides the deployment-mode selection mechanism (MICROCEPH vs
NONE) persisted in clusterd, following the OVN provider pattern.
"""

from __future__ import annotations

import abc
import enum
import logging
from typing import TYPE_CHECKING, Any

import pydantic

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, Status
from sunbeam.core.questions import load_answers, write_answers

if TYPE_CHECKING:
    from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)

CLUSTERD_CONFIG_KEY = "CephConfig"


class CephDeploymentMode(enum.StrEnum):
    """How Ceph storage infrastructure is provided."""

    MICROCEPH = "microceph"
    NONE = "none"


DEFAULT_MODE = CephDeploymentMode.MICROCEPH


class CephConfig(pydantic.BaseModel):
    """Persisted ceph deployment configuration."""

    mode: CephDeploymentMode | None = None


def load_ceph_config(client: Client) -> CephConfig:
    """Load the Ceph deployment configuration from clusterd.

    :param client: the Sunbeam clusterd client
    :return: the Ceph deployment configuration
    """
    answers = load_answers(client, CLUSTERD_CONFIG_KEY)
    return CephConfig.model_validate(answers)


def write_ceph_config(client: Client, config: CephConfig) -> None:
    """Write the Ceph deployment configuration to clusterd.

    :param client: the Sunbeam clusterd client
    :param config: the Ceph deployment configuration
    """
    write_answers(client, CLUSTERD_CONFIG_KEY, config.model_dump())


def is_microceph_necessary(client: Client) -> bool:
    """Check whether microceph should be deployed.

    :param client: the Sunbeam clusterd client
    :return: True if the deployment mode is MICROCEPH (or unset, defaulting
        to MICROCEPH for backward compatibility)
    """
    config = load_ceph_config(client)
    mode = config.mode if config.mode is not None else DEFAULT_MODE
    return mode == CephDeploymentMode.MICROCEPH


class SetCephProviderStep(BaseStep):
    """Persist the Ceph deployment mode in clusterd."""

    def __init__(self, client: Client, *, no_microceph: bool = False):
        super().__init__(
            "Set Ceph provider",
            "Setting Ceph provider in deployment configuration",
        )
        self.client = client
        self.wanted_mode = (
            CephDeploymentMode.NONE if no_microceph else CephDeploymentMode.MICROCEPH
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Skip if the mode is already set to the desired value."""
        config = load_ceph_config(self.client)
        if config.mode == self.wanted_mode:
            LOG.debug(
                "Ceph deployment mode is already set to %s",
                self.wanted_mode,
            )
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Write the desired mode to clusterd."""
        config = load_ceph_config(self.client)
        config.mode = self.wanted_mode
        write_ceph_config(self.client, config)
        return Result(ResultType.COMPLETED)


class CephProvider(abc.ABC):
    """Abstract interface for Ceph storage infrastructure providers.

    This defines the contract between the Sunbeam orchestration code
    and whatever provides Ceph services (microceph, remote ceph, etc.).

    Implementations are responsible for providing terraform variables
    that the control plane and cinder-volume steps need.
    """

    @abc.abstractmethod
    def get_control_plane_tfvars(
        self,
        model_with_owner: str,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for the control plane.

        Called by DeployControlPlaneStep / ReapplyControlPlaneStep to
        compute the ceph portion of the openstack terraform variables.

        :param model_with_owner: Juju model name with owner prefix
            (e.g. "admin/openstack-machines").
        :param storage_node_count: Number of nodes with the storage role.
        :returns: dict with at minimum:
            - enable-ceph: bool
            - ceph-offer-url: str (when enabled)
            - ceph-nfs-offer-url: str (when enabled)
            - ceph-rgw-offer-url: str (when enabled)
            - ceph-osd-replication-count: int (when enabled)
        """

    @abc.abstractmethod
    def get_cinder_volume_tfvars(
        self,
        deployment: Deployment,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for cinder-volume.

        Called by DeployCinderVolumeApplicationStep to configure the
        cinder-volume-ceph subordinate charm.

        :param deployment: The active deployment (for accessing terraform helpers).
        :param storage_node_count: Number of nodes with the storage role.
        :returns: dict with at minimum:
            - charm_cinder_volume_ceph_config: dict (ceph-osd-replication-count)
            - ceph-application-name: str (when storage nodes exist)
        """

    @abc.abstractmethod
    def get_replica_count(self, storage_node_count: int) -> int:
        """Return the Ceph OSD replication count.

        :param storage_node_count: Number of nodes with the storage role.
        """

    @property
    @abc.abstractmethod
    def application_name(self) -> str:
        """Return the Juju application name (e.g. 'microceph')."""

    @property
    @abc.abstractmethod
    def status_column(self) -> str:
        """Return the column name for cluster status display."""

    @property
    @abc.abstractmethod
    def terraform_plan_name(self) -> str:
        """Return the terraform plan key (e.g. 'microceph-plan')."""


class NoCephProvider(CephProvider):
    """Provider used when no local Ceph infrastructure is deployed.

    Returns ``enable-ceph: False`` and empty cinder-volume ceph config
    so that the control plane and cinder-volume steps work correctly
    without any microceph deployment.
    """

    def get_control_plane_tfvars(
        self,
        model_with_owner: str,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return disabled Ceph terraform variables."""
        return {"enable-ceph": False}

    def get_cinder_volume_tfvars(
        self,
        deployment: Deployment,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return empty cinder-volume ceph config."""
        return {"charm_cinder_volume_ceph_config": {}}

    def get_replica_count(self, storage_node_count: int) -> int:
        """Return zero replicas when no Ceph is deployed."""
        return 0

    @property
    def application_name(self) -> str:
        """Return empty application name."""
        return ""

    @property
    def status_column(self) -> str:
        """Return empty status column."""
        return ""

    @property
    def terraform_plan_name(self) -> str:
        """Return empty terraform plan name."""
        return ""
