# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from typing import Any

import tenacity

from sunbeam.clusterd.client import Client
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    convert_retry_failure_as_result,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.core.ovn import OvnProvider
from sunbeam.core.role_assignments import (
    build_microovn_role_mapping,
    dump_role_mapping,
)
from sunbeam.core.steps import DeployMachineApplicationStep, RemoveMachineUnitsStep
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.feature_gates import split_roles_enabled

CONFIG_KEY = "TerraformVarsRoleDistributorPlan"
APPLICATION = "role-distributor"
ROLE_DISTRIBUTOR_UNIT_TIMEOUT = 600
NO_CONTROL_MACHINE_MESSAGE = "role-distributor requires at least one control machine"

LOG = logging.getLogger(__name__)


def _manifest_config(manifest: Manifest) -> dict[str, Any]:
    charm_manifest: CharmManifest | None = manifest.core.software.charms.get(
        APPLICATION
    )
    if not charm_manifest or not charm_manifest.config:
        return {}
    config = dict(charm_manifest.config)
    config.pop("role-mapping", None)
    return config


def _role_distributor_extra_tfvars(
    deployment: Deployment,
    client: Client,
    manifest: Manifest,
    model: str,
    microovn_machine_ids: list[str] | None = None,
    role_distributor_machine_ids: list[str] | None = None,
) -> dict[str, Any]:
    if microovn_machine_ids is None:
        microovn_machine_ids = _microovn_machine_ids(deployment)
    if role_distributor_machine_ids is None:
        role_distributor_machine_ids = _control_machine_ids(client)

    role_mapping = build_microovn_role_mapping(
        client,
        model,
        split_roles_enabled(),
        microovn_machine_ids,
        assign_central_roles=(
            deployment.get_ovn_manager().get_provider() == OvnProvider.MICROOVN
        ),
    )

    config = _manifest_config(manifest)
    config["role-mapping"] = dump_role_mapping(role_mapping)

    return {
        "role_distributor_machine_ids": role_distributor_machine_ids[:1],
        # Prevent the generic machine application step from computing and
        # persisting a second machine placement variable for this plan.
        "machine_ids": [],
        "charm_role_distributor_config": config,
    }


def _microovn_machine_ids(deployment: Deployment) -> list[str]:
    """Return machines that currently consume MicroOVN role assignments."""
    return deployment.get_ovn_manager().get_machines()


def _control_machine_ids(client: Client) -> list[str]:
    """Return valid control-node machine IDs for role-distributor placement."""
    return sorted(
        {
            str(node.get("machineid"))
            for node in client.cluster.list_nodes_by_role("control")
            if node.get("machineid") not in (-1, None)
        }
    )


class DeployRoleDistributorApplicationStep(DeployMachineApplicationStep):
    """Deploy role-distributor and publish topology-derived assignments."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            APPLICATION,
            model,
            [],
            "Deploy role distributor",
            "Deploying role distributor",
        )

    def get_accepted_application_status(self) -> list[str]:
        """Accept waiting while requirer charms join the relation."""
        return ["active", "waiting"]

    def is_skip(self, context: StepContext) -> Result:
        """Skip when there are no MicroOVN machines to assign roles to."""
        if not _microovn_machine_ids(self.deployment):
            return Result(ResultType.SKIPPED)
        if not _control_machine_ids(self.client):
            return Result(ResultType.FAILED, NO_CONTROL_MACHINE_MESSAGE)
        return Result(ResultType.COMPLETED)

    def extra_tfvars(self) -> dict[str, Any]:
        """Extra terraform vars to pass to terraform apply."""
        return _role_distributor_extra_tfvars(
            self.deployment,
            self.client,
            self.manifest,
            self.model,
        )


class ReapplyRoleDistributorApplicationStep(BaseStep):
    """Reapply role-distributor placement and topology-derived config."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            "Reapply role distributor Terraform plan",
            "Reapplying role distributor Terraform plan",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, context: StepContext) -> Result:
        """Apply Terraform configuration for role-distributor."""
        microovn_machine_ids = _microovn_machine_ids(self.deployment)
        if not microovn_machine_ids:
            return Result(ResultType.SKIPPED)
        role_distributor_machine_ids = _control_machine_ids(self.client)
        if not role_distributor_machine_ids:
            return Result(ResultType.FAILED, NO_CONTROL_MACHINE_MESSAGE)

        extra_tfvars = _role_distributor_extra_tfvars(
            self.deployment,
            self.client,
            self.manifest,
            self.model,
            microovn_machine_ids,
            role_distributor_machine_ids,
        )
        extra_tfvars["machine_model_uuid"] = self.jhelper.get_model_uuid(self.model)

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
                reporter=context.reporter,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_ready(
                APPLICATION,
                self.model,
                accepted_status=["active", "waiting"],
                timeout=ROLE_DISTRIBUTOR_UNIT_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.warning("Timed out waiting for role-distributor reapply: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveRoleDistributorUnitsStep(RemoveMachineUnitsStep):
    """Remove role-distributor unit from a machine before node removal."""

    def __init__(
        self, client: Client, names: list[str] | str, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            client,
            names,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            model,
            "Remove role-distributor unit",
            "Removing role-distributor unit from machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return ROLE_DISTRIBUTOR_UNIT_TIMEOUT
