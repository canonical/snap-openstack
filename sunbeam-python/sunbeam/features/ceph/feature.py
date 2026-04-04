# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.core.ceph import (
    INTERNAL_CEPH_BACKEND_NAME,
    SetCephProviderStep,
    is_internal_ceph_enabled,
)
from sunbeam.core.common import BaseStep, run_plan, update_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import FeatureConfig
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.features.microceph.steps import (
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    DestroyMicrocephApplicationStep,
    ceph_replica_scale,
)
from sunbeam.steps.openstack import DeployControlPlaneStep
from sunbeam.storage.backends.internal_ceph.backend import (
    InternalCephBackend,
    InternalCephConfig,
)
from sunbeam.storage.steps import (
    DeploySpecificCinderVolumeStep,
    DestroySpecificCinderVolumeStep,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

LOG = logging.getLogger(__name__)
console = Console()

DEFAULT_STORAGE_RECONCILED_KEY = "default_storage_reconciled"


class CephFeature(EnableDisableFeature):
    version = Version("0.0.1")

    name = "ceph"

    def __init__(self) -> None:
        super().__init__()
        self._default_storage_kwargs: dict[str, Any] = {}

    def _get_internal_ceph_backend(self) -> InternalCephBackend:
        """Create and return an InternalCephBackend instance."""
        return InternalCephBackend()

    def _get_provider_specific_steps(
        self, deployment: Deployment, **kwargs: Any
    ) -> list[BaseStep]:
        """Return provider-specific storage setup steps."""
        client = deployment.get_client()
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest(self.user_manifest)
        model = deployment.openstack_machines_model

        if deployment.type == "local":
            node_name = kwargs.get("node_name")
            if not node_name:
                return []
            return [
                ConfigureMicrocephOSDStep(
                    client,
                    node_name,
                    jhelper,
                    model,
                    manifest=manifest,
                    accept_defaults=kwargs.get("accept_defaults", False),
                )
            ]

        if deployment.type == "maas":
            maas_client = kwargs.get("maas_client")
            storage = kwargs.get("storage", [])
            if maas_client is None or not storage:
                return []
            from sunbeam.provider.maas.steps import MaasConfigureMicrocephOSDStep

            return [
                MaasConfigureMicrocephOSDStep(
                    client,
                    maas_client,
                    jhelper,
                    storage,
                    manifest,
                    model,
                )
            ]

        return []

    def _get_internal_ceph_enable_steps(self, deployment: Deployment) -> list[BaseStep]:
        """Return the steps to register the internal-ceph backend."""
        backend = self._get_internal_ceph_backend()
        backend.register_terraform_plan(deployment)

        client = deployment.get_client()
        storage_tfhelper = deployment.get_tfhelper(backend.tfplan)
        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest(self.user_manifest)

        # Compute replication count from storage node count
        storage_nodes = client.cluster.list_nodes_by_role("storage")
        replication_count = ceph_replica_scale(len(storage_nodes))

        # Store config in clusterd
        config = InternalCephConfig.model_validate(
            {"ceph_osd_replication_count": replication_count}, by_name=True
        )
        config_key = backend.config_key(INTERNAL_CEPH_BACKEND_NAME)
        update_config(
            client, config_key, config.model_dump(exclude_none=True, by_alias=True)
        )

        return [
            TerraformInitStep(storage_tfhelper),
            TerraformInitStep(openstack_tfhelper),
            DeploySpecificCinderVolumeStep(
                deployment,
                client,
                storage_tfhelper,
                jhelper,
                manifest,
                INTERNAL_CEPH_BACKEND_NAME,
                backend,
                deployment.openstack_machines_model,
            ),
            backend.create_deploy_step(
                deployment,
                client,
                storage_tfhelper,
                jhelper,
                manifest,
                config.model_dump(exclude_none=True, by_alias=True),
                INTERNAL_CEPH_BACKEND_NAME,
                deployment.openstack_machines_model,
                accept_defaults=True,
            ),
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                manifest,
                "auto",
                deployment.openstack_machines_model,
            ),
        ]

    def _get_internal_ceph_disable_steps(
        self, deployment: Deployment
    ) -> list[BaseStep]:
        """Return the steps to remove the internal-ceph backend.

        Does NOT include DeployControlPlaneStep — the caller must run
        SetCephProviderStep(no_default_storage=True) and then construct
        DeployControlPlaneStep separately so it picks up NoCephProvider.
        """
        backend = self._get_internal_ceph_backend()
        backend.register_terraform_plan(deployment)

        client = deployment.get_client()
        storage_tfhelper = deployment.get_tfhelper(backend.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest()

        return [
            TerraformInitStep(storage_tfhelper),
            backend.create_destroy_step(
                deployment,
                client,
                storage_tfhelper,
                jhelper,
                manifest,
                INTERNAL_CEPH_BACKEND_NAME,
                deployment.openstack_machines_model,
            ),
            DestroySpecificCinderVolumeStep(
                deployment,
                client,
                storage_tfhelper,
                jhelper,
                manifest,
                INTERNAL_CEPH_BACKEND_NAME,
                backend,
                deployment.openstack_machines_model,
            ),
        ]

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Run plans to enable ceph support via microceph."""
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper("microceph-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest(self.user_manifest)
        plan: list[BaseStep] = [
            SetCephProviderStep(client),
            TerraformInitStep(tfhelper),
            DeployMicrocephApplicationStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            ),
        ]
        plan.extend(
            self._get_provider_specific_steps(
                deployment, **self._default_storage_kwargs
            )
        )
        plan.extend(self._get_internal_ceph_enable_steps(deployment))
        run_plan(plan, console, show_hints)
        click.echo("Ceph enabled.")

    def post_enable(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Mark explicit Ceph enablement as fully reconciled."""
        self.update_feature_info(
            deployment.get_client(),
            {DEFAULT_STORAGE_RECONCILED_KEY: "true"},
        )

    def _is_default_storage_reconciled(self, client: Any) -> bool:
        """Return whether default-storage lifecycle has been fully reconciled."""
        info = self.get_feature_info(client)
        return (
            info.get("enabled", "false").lower() == "true"
            and info.get(DEFAULT_STORAGE_RECONCILED_KEY, "false").lower() == "true"
        )

    def enable_default_storage(
        self, deployment: Deployment, show_hints: bool, **kwargs: Any
    ) -> None:
        """Enable the default ceph-backed storage path."""
        client = deployment.get_client()
        if not is_internal_ceph_enabled(client):
            return
        if self._is_default_storage_reconciled(client):
            return

        self._default_storage_kwargs = kwargs
        try:
            self.run_enable_plans(deployment, FeatureConfig(), show_hints)
            self.update_feature_info(
                client,
                {
                    "enabled": "true",
                    DEFAULT_STORAGE_RECONCILED_KEY: "true",
                },
            )
        finally:
            self._default_storage_kwargs = {}

    def on_join(self, deployment: Deployment, node: Any, **kwargs: Any) -> None:
        """Reconcile default ceph-backed storage when a storage node joins."""
        roles = kwargs.get("roles")
        if roles is None and isinstance(node, dict):
            roles = node.get("role", [])
        if "storage" not in (roles or []):
            return

        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper("microceph-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest(self.user_manifest)
        show_hints = kwargs.get("show_hints", False)

        plan: list[BaseStep] = [
            TerraformInitStep(tfhelper),
            DeployMicrocephApplicationStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            ),
        ]
        plan.extend(self._get_provider_specific_steps(deployment, **kwargs))
        run_plan(plan, console, show_hints)

        # When the feature is already reconciled, reapply the storage
        # backend and control plane so the new node gets cinder-volume
        # placement and the replica count is updated.
        if self._is_default_storage_reconciled(client):
            run_plan(
                self._get_internal_ceph_enable_steps(deployment),
                console,
                show_hints,
            )

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable ceph support and teardown microceph.

        Three phases to minimise inconsistency on partial failure:
        1. Destroy internal-ceph backend and cinder-volume (mode still MICROCEPH)
        2. Persist mode=NONE, then reapply control plane (sees NoCephProvider)
        3. Destroy MicroCeph application
        """
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper("microceph-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest()

        # Phase 1: destroy backend (mode stays MICROCEPH — safe to retry)
        run_plan(
            self._get_internal_ceph_disable_steps(deployment),
            console,
            show_hints,
        )

        # Phase 2: flip mode, then reapply control plane with NoCephProvider.
        # DeployControlPlaneStep must be constructed AFTER SetCephProviderStep
        # runs so it picks up NoCephProvider via deployment.get_ceph_provider().
        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        run_plan(
            [SetCephProviderStep(client, no_default_storage=True)],
            console,
            show_hints,
        )
        run_plan(
            [
                TerraformInitStep(openstack_tfhelper),
                DeployControlPlaneStep(
                    deployment,
                    openstack_tfhelper,
                    jhelper,
                    manifest,
                    "auto",
                    deployment.openstack_machines_model,
                ),
            ],
            console,
            show_hints,
        )

        # Phase 3: destroy MicroCeph
        run_plan(
            [
                TerraformInitStep(tfhelper),
                DestroyMicrocephApplicationStep(
                    client,
                    tfhelper,
                    jhelper,
                    manifest,
                    deployment.openstack_machines_model,
                ),
            ],
            console,
            show_hints,
        )
        click.echo("Ceph disabled.")

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable ceph support."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="Force disable ceph. WARNING: This will result in data loss.",
    )
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(
        self, deployment: Deployment, force: bool = False, show_hints: bool = False
    ) -> None:
        """Disable ceph support."""
        if not force:
            raise click.ClickException(
                "Disabling ceph will result in data loss. Use --force to confirm."
            )
        self.disable_feature(deployment, show_hints)
