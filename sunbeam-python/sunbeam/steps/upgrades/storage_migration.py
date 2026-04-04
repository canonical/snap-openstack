# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Migration step for cinder-volume terraform state.

Moves resources from the retired ``deploy-cinder-volume`` plan into the
unified ``deploy-storage`` plan and registers the ``internal-ceph``
backend in clusterd.
"""

import logging

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    StorageBackendNotFoundException,
)
from sunbeam.core.ceph import INTERNAL_CEPH_BACKEND_NAME, is_internal_ceph_enabled
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    StepContext,
    read_config,
    update_config,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.features.ceph.feature import DEFAULT_STORAGE_RECONCILED_KEY
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.features.microceph.steps import ceph_replica_scale
from sunbeam.storage.backends.internal_ceph.backend import (
    InternalCephBackend,
    InternalCephConfig,
)
from sunbeam.storage.base import PRINCIPAL_HA_APPLICATION
from sunbeam.storage.steps import (
    STORAGE_BACKEND_TFVAR_CONFIG_KEY,
    get_mandatory_control_plane_offers,
    get_optional_control_plane_offers,
)
from sunbeam.versions import CINDER_VOLUME_CHARM

LOG = logging.getLogger(__name__)

STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY = "StorageBackendLegacyImportIds"

LEGACY_TO_STORAGE_IMPORT_ADDRESS_MAP = {
    "juju_application.cinder-volume": (
        'module.cinder-volume["cinder-volume"].juju_application.cinder-volume'
    ),
    "juju_offer.storage-backend-offer": (
        'module.cinder-volume["cinder-volume"].juju_offer.storage-backend-offer'
    ),
    "juju_integration.cinder-volume-identity[0]": (
        'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-identity[0]'
    ),
    "juju_integration.cinder-volume-amqp[0]": (
        'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-amqp[0]'
    ),
    "juju_integration.cinder-volume-database[0]": (
        'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-database[0]'
    ),
    "juju_integration.cinder-volume-cert-distributor[0]": (
        'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-cert-distributor[0]'
    ),
    "juju_application.cinder-volume-ceph": (
        'module.backends["internal-ceph"].juju_application.storage-backend'
    ),
    "juju_integration.cinder-volume-ceph-to-cinder-volume": (
        'module.backends["internal-ceph"].juju_integration.storage-backend-to-cinder-volume'
    ),
    "juju_integration.cinder-volume-ceph-to-ceph[0]": (
        'module.backends["internal-ceph"].juju_integration.backend-extra-integration["microceph-ceph"]'
    ),
}


class BackfillCephFeatureStateStep(BaseStep):
    """Backfill Ceph feature metadata for existing internal Ceph deployments."""

    def __init__(self, deployment: Deployment, client: Client):
        super().__init__(
            "Backfill Ceph feature state",
            "Backfilling Ceph feature metadata for internal Ceph deployments",
        )
        self.deployment = deployment
        self.client = client

    def run(self, context: StepContext) -> Result:
        """Backfill Ceph feature metadata when internal Ceph is managed."""
        if not is_internal_ceph_enabled(self.client):
            return Result(ResultType.COMPLETED)

        feature = self.deployment.get_feature_manager().resolve_feature("ceph")
        if not isinstance(feature, EnableDisableFeature):
            LOG.debug("Failed to resolve ceph feature for state backfill.")
            return Result(ResultType.COMPLETED)

        feature.update_feature_info(
            self.client,
            {
                "enabled": "true",
                DEFAULT_STORAGE_RECONCILED_KEY: "true",
            },
        )
        return Result(ResultType.COMPLETED)


class ImportCephResourcesToStorageFrameworkStep(BaseStep):
    """Import legacy internal-Ceph resources into the storage plan state."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            "Import Ceph resources to storage framework",
            "Importing legacy Ceph resources into storage-backend Terraform state",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Skip when there is no migrated internal-ceph backend to import."""
        try:
            tfvars = read_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            return Result(ResultType.SKIPPED, "No storage backend config found.")

        if INTERNAL_CEPH_BACKEND_NAME not in tfvars.get("backends", {}):
            return Result(ResultType.SKIPPED, "No internal-ceph backend to import.")

        if not tfvars.get("cinder-volumes"):
            return Result(
                ResultType.SKIPPED,
                "No cinder-volume resources configured for import.",
            )

        return Result(ResultType.COMPLETED)

    def _build_imports(
        self,
        tfvars: dict,
        *,
        model_uuid: str,
        model_name: str,
    ) -> list[tuple[str, str]]:
        """Build the list of legacy Ceph resources to import."""
        # Use the known HA principal — legacy Ceph always deployed under
        # cinder-volume (HA).  Do NOT use next(iter(...)) which could
        # pick a non-HA principal from another backend.
        principal = PRINCIPAL_HA_APPLICATION
        cinder_volume = tfvars["cinder-volumes"][principal]
        backend = tfvars["backends"][INTERNAL_CEPH_BACKEND_NAME]
        backend_application_name = backend.get("application_name", "cinder-volume-ceph")

        imports = {
            f'module.cinder-volume["{principal}"].juju_application.cinder-volume': (
                f"{model_uuid}:{cinder_volume['application_name']}"
            ),
            f'module.cinder-volume["{principal}"].juju_offer.storage-backend-offer': (
                f"{model_name}.{cinder_volume['application_name']}"
            ),
            'module.backends["internal-ceph"].juju_application.storage-backend': (
                f"{model_uuid}:{backend_application_name}"
            ),
            (
                'module.backends["internal-ceph"].juju_integration.'
                "storage-backend-to-cinder-volume"
            ): (
                f"{model_uuid}:{principal}:cinder-volume:"
                f"{backend_application_name}:cinder-volume"
            ),
            (
                'module.backends["internal-ceph"].juju_integration.'
                'backend-extra-integration["microceph-ceph"]'
            ): (f"{model_uuid}:microceph:ceph:{backend_application_name}:ceph"),
        }

        try:
            legacy_import_ids = read_config(
                self.client, STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY
            )
        except ConfigItemNotFoundException:
            legacy_import_ids = {}

        for old_address, import_id in legacy_import_ids.items():
            if new_address := LEGACY_TO_STORAGE_IMPORT_ADDRESS_MAP.get(old_address):
                imports[new_address] = import_id

        return list(imports.items())

    def run(self, context: StepContext) -> Result:
        """Import migrated Ceph resources into the new storage plan state."""
        try:
            tfvars = read_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        self.tfhelper.write_tfvars(tfvars)

        model_info = self.jhelper.get_model(self.model)
        imports = self._build_imports(
            tfvars,
            model_uuid=model_info["model-uuid"],
            model_name=model_info["name"],
        )

        try:
            try:
                existing_resources = set(self.tfhelper.state_list())
            except TerraformException as e:
                if "No state file was found" in str(e):
                    existing_resources = set()
                else:
                    raise
            for address, resource_id in imports:
                if address in existing_resources:
                    continue
                self.tfhelper.import_resource(address, resource_id)
                existing_resources.add(address)
        except TerraformException as e:
            LOG.error("Failed to import legacy Ceph resources: %s", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class MigrateCinderVolumeToStorageFrameworkStep(BaseStep):
    """Migrate cinder-volume resources to the storage framework.

    Existing deployments manage cinder-volume and cinder-volume-ceph via
    the ``deploy-cinder-volume`` Terraform plan.  This step:

    1. Removes all resources from the old plan's state (no destruction).
    2. Registers the ``internal-ceph`` backend in clusterd.
    3. Populates ``TerraformVarsStorageBackends`` so that the
       ``deploy-storage`` plan can adopt the running Juju resources.

    The subsequent ``ReapplyStorageBackendTerraformPlanStep`` in the
    upgrade flow runs ``terraform apply`` on the new plan, which
    reconciles the existing Juju applications into the new state.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        old_tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            "Migrate cinder-volume to storage framework",
            "Migrating cinder-volume terraform state to unified storage plan",
        )
        self.deployment = deployment
        self.client = client
        self.old_tfhelper = old_tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model

    def _old_plan_has_resources(self) -> bool:
        """Return True if the old cinder-volume plan has resources."""
        try:
            resources = self.old_tfhelper.state_list()
            return len(resources) > 0
        except TerraformException:
            LOG.debug(
                "Failed to list old cinder-volume plan state",
                exc_info=True,
            )
            return False

    def is_skip(self, context: StepContext) -> Result:
        """Skip when migration is not needed.

        Migration is skipped when the old plan has no resources.
        When the storage framework already has backends (e.g. PureStorage),
        the migration merges the internal-ceph entries into the existing
        config rather than overwriting it.
        """
        if not self._old_plan_has_resources():
            LOG.debug("Old cinder-volume plan has no resources; skipping migration.")
            return Result(
                ResultType.SKIPPED,
                "Old cinder-volume plan has no resources.",
            )

        return Result(ResultType.COMPLETED)

    def _clear_old_state(self) -> None:
        """Remove all resources from the old terraform plan state."""
        resources = self.old_tfhelper.state_list()
        for resource in resources:
            # Skip data sources (they are read-only references)
            if resource.startswith("data."):
                continue
            LOG.debug("Removing resource %r from old cinder-volume plan", resource)
            self.old_tfhelper.state_rm(resource)

    def _capture_legacy_import_ids(self) -> dict[str, str]:
        """Capture exact legacy resource IDs from the old terraform state."""
        state = self.old_tfhelper.pull_state()
        import_ids: dict[str, str] = {}

        for resource in state.get("resources", []):
            mode = resource.get("mode")
            resource_type = resource.get("type")
            resource_name = resource.get("name")
            if mode != "managed" or not resource_type or not resource_name:
                continue

            for instance in resource.get("instances", []):
                index_key = instance.get("index_key")
                address = f"{resource_type}.{resource_name}"
                if index_key is not None:
                    address = f"{address}[{index_key}]"
                attributes = instance.get("attributes", {})
                import_id = attributes.get("id")
                if import_id:
                    import_ids[address] = import_id

        return import_ids

    def _register_internal_ceph_backend(self, model_uuid: str) -> None:
        """Register the internal-ceph backend in clusterd."""
        backend = InternalCephBackend()
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        replication_count = ceph_replica_scale(len(storage_nodes))

        config = InternalCephConfig.model_validate(
            {"ceph_osd_replication_count": replication_count}, by_name=True
        )
        config_dict = config.model_dump(exclude_none=True, by_alias=True)
        config_key = backend.config_key(INTERNAL_CEPH_BACKEND_NAME)
        update_config(self.client, config_key, config_dict)
        try:
            self.client.cluster.get_storage_backend(INTERNAL_CEPH_BACKEND_NAME)
            self.client.cluster.update_storage_backend(
                name=INTERNAL_CEPH_BACKEND_NAME,
                backend_type=backend.backend_type,
                config=config_dict,
                principal=backend.principal_application,
                model_uuid=model_uuid,
            )
        except StorageBackendNotFoundException:
            self.client.cluster.add_storage_backend(
                name=INTERNAL_CEPH_BACKEND_NAME,
                backend_type=backend.backend_type,
                config=config_dict,
                principal=backend.principal_application,
                model_uuid=model_uuid,
            )

        backend.enable_backend(self.client)

    def _build_storage_tfvars(self, model_uuid: str) -> dict:
        """Build TerraformVarsStorageBackends for the new plan.

        Merges the internal-ceph backend and its HA principal into any
        existing storage framework config so that third-party backends
        (PureStorage, Hitachi, etc.) are preserved.
        """
        # Read existing config to merge into
        try:
            tfvars = read_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars.setdefault("model", model_uuid)
        tfvars.setdefault("cinder-volumes", {})
        tfvars.setdefault("backends", {})

        backend = InternalCephBackend()

        # --- cinder-volume HA principal entry ---
        principal = PRINCIPAL_HA_APPLICATION

        # Only create the HA principal entry if it doesn't already exist;
        # another HA backend (e.g. PureStorage) may have set it up already.
        if principal not in tfvars["cinder-volumes"]:
            storage_nodes = self.client.cluster.list_nodes_by_role(
                Role.STORAGE.name.lower()
            )
            machine_ids = sorted(
                (str(node["machineid"]) for node in storage_nodes), key=int
            )

            cinder_volume_charm = self.manifest.core.software.charms.get(
                CINDER_VOLUME_CHARM
            )
            charm_config: dict = {}
            charm_channel = None
            charm_revision = None
            if cinder_volume_charm:
                charm_channel = cinder_volume_charm.channel
                charm_revision = cinder_volume_charm.revision
                if cinder_volume_charm.config:
                    charm_config.update(cinder_volume_charm.config)

            charm_config["snap-name"] = backend.snap_name

            cinder_volume_entry = {
                "application_name": principal,
                "charm_channel": charm_channel,
                "charm_revision": charm_revision,
                "charm_config": charm_config,
                "machine_ids": machine_ids,
                "endpoint_bindings": [
                    {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                    {
                        "endpoint": "amqp",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "database",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "cinder-volume",
                        "space": self.deployment.get_space(Networks.MANAGEMENT),
                    },
                    {
                        "endpoint": "identity-credentials",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "receive-ca-cert",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "storage-backend",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                ],
            }

            # Add control plane offers
            try:
                openstack_tfhelper = self.deployment.get_tfhelper("openstack-plan")
                cinder_volume_entry.update(
                    get_mandatory_control_plane_offers(openstack_tfhelper)
                )
                cinder_volume_entry.update(
                    get_optional_control_plane_offers(openstack_tfhelper)
                )
            except Exception:
                LOG.debug(
                    "Could not get control plane offers; "
                    "they will be populated on next apply",
                    exc_info=True,
                )

            # Telemetry flag
            feature_manager = self.deployment.get_feature_manager()
            cinder_volume_entry["enable-telemetry-notifications"] = (
                feature_manager.is_feature_enabled(self.deployment, "telemetry")
            )

            tfvars["cinder-volumes"][principal] = cinder_volume_entry

        # --- internal-ceph backend entry (always merged) ---
        storage_nodes = self.client.cluster.list_nodes_by_role(
            Role.STORAGE.name.lower()
        )
        replication_count = ceph_replica_scale(len(storage_nodes))
        config = InternalCephConfig.model_validate(
            {"ceph_osd_replication_count": replication_count}, by_name=True
        )
        backend_tfvars = backend.build_terraform_vars(
            self.deployment,
            self.manifest,
            INTERNAL_CEPH_BACKEND_NAME,
            config,
        )

        tfvars["backends"][INTERNAL_CEPH_BACKEND_NAME] = backend_tfvars

        return tfvars

    def run(self, context: StepContext) -> Result:
        """Execute the migration."""
        model_info = self.jhelper.get_model(self.model)
        model_uuid = model_info["model-uuid"]

        try:
            legacy_import_ids = self._capture_legacy_import_ids()
            update_config(
                self.client,
                STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY,
                legacy_import_ids,
            )
        except TerraformException as e:
            LOG.error("Failed to capture legacy cinder-volume state IDs: %s", e)
            return Result(
                ResultType.FAILED,
                f"Failed to capture legacy resource IDs: {e}",
            )

        # Step 1: Clear old plan state (removes tracking, not Juju resources)
        try:
            self._clear_old_state()
        except TerraformException as e:
            LOG.error("Failed to clear old cinder-volume plan state: %s", e)
            return Result(
                ResultType.FAILED,
                f"Failed to clear old terraform state: {e}",
            )

        # Step 2: Register internal-ceph backend metadata in clusterd
        try:
            self._register_internal_ceph_backend(model_uuid)
        except Exception as e:
            LOG.error("Failed to register internal-ceph backend: %s", e)
            return Result(
                ResultType.FAILED,
                f"Failed to register internal-ceph backend: {e}",
            )

        # Step 3: Populate TerraformVarsStorageBackends
        try:
            tfvars = self._build_storage_tfvars(model_uuid)
            update_config(self.client, STORAGE_BACKEND_TFVAR_CONFIG_KEY, tfvars)
        except Exception as e:
            LOG.error("Failed to populate storage backend tfvars: %s", e)
            return Result(
                ResultType.FAILED,
                f"Failed to populate storage backend tfvars: {e}",
            )

        LOG.info(
            "Successfully migrated cinder-volume terraform state "
            "to the unified storage framework."
        )
        return Result(ResultType.COMPLETED)
