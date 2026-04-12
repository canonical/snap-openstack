# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    StorageBackendNotFoundException,
)
from sunbeam.core.ceph import INTERNAL_CEPH_BACKEND_NAME
from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.steps.upgrades.storage_migration import (
    STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY,
    BackfillCephFeatureStateStep,
    ImportCephResourcesToStorageFrameworkStep,
    MigrateCinderVolumeToStorageFrameworkStep,
)
from sunbeam.storage.steps import STORAGE_BACKEND_TFVAR_CONFIG_KEY

_MODULE = "sunbeam.steps.upgrades.storage_migration"


@pytest.fixture
def deployment():
    dep = Mock()
    dep.get_space.return_value = "mgmt"
    dep.openstack_machines_model = "machines"
    feature_manager = Mock()
    feature_manager.is_feature_enabled.return_value = False
    dep.get_feature_manager.return_value = feature_manager
    openstack_tfhelper = Mock()
    openstack_tfhelper.output.return_value = {
        "keystone-offer-url": "admin/openstack.keystone",
        "cinder-volume-database-offer-url": "admin/openstack.cinder-db",
        "rabbitmq-offer-url": "admin/openstack.rabbitmq",
        "cert-distributor-offer-url": None,
    }
    dep.get_tfhelper.return_value = openstack_tfhelper
    return dep


@pytest.fixture
def client():
    c = Mock()
    c.cluster.list_nodes_by_role.return_value = [
        {"machineid": "0"},
        {"machineid": "1"},
        {"machineid": "2"},
    ]

    def _get_config(key):
        if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
            raise ConfigItemNotFoundException("not found")
        if key == "StorageBackendsEnabled":
            return "[]"
        raise ConfigItemNotFoundException("not found")

    c.cluster.get_config.side_effect = _get_config
    return c


@pytest.fixture
def old_tfhelper():
    helper = Mock()
    helper.pull_state.return_value = {"resources": []}
    return helper


@pytest.fixture
def jhelper():
    h = Mock()
    h.get_model.return_value = {
        "model-uuid": "test-model-uuid",
        "name": "admin/openstack",
    }
    return h


@pytest.fixture
def manifest():
    m = Mock()
    charm = Mock()
    charm.channel = "2024.1/stable"
    charm.revision = None
    charm.config = {}
    m.core.software.charms = {"cinder-volume": charm}
    m.storage.root = {}
    return m


@pytest.fixture
def step(deployment, client, old_tfhelper, jhelper, manifest):
    return MigrateCinderVolumeToStorageFrameworkStep(
        deployment=deployment,
        client=client,
        old_tfhelper=old_tfhelper,
        jhelper=jhelper,
        manifest=manifest,
        model="machines",
    )


class TestMigrateCinderVolumeIsSkip:
    """Tests for is_skip logic."""

    def test_skip_when_old_plan_has_no_resources(self, step, old_tfhelper):
        """Migration is skipped when old plan has no resources."""
        old_tfhelper.state_list.return_value = []

        result = step.is_skip(Mock())

        assert result.result_type == ResultType.SKIPPED

    def test_skip_when_old_plan_state_list_fails(self, step, old_tfhelper):
        """Migration is skipped when listing old plan state fails."""
        old_tfhelper.state_list.side_effect = TerraformException("state list failed")

        result = step.is_skip(Mock())

        assert result.result_type == ResultType.SKIPPED

    def test_not_skipped_when_storage_framework_already_configured(
        self, step, old_tfhelper, client
    ):
        """Migration runs even when storage framework has backends (merges)."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]

        def _get_config(key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return '{"backends": {"internal-ceph": {}}, "cinder-volumes": {}}'
            if key == "StorageBackendsEnabled":
                return "[]"
            raise ConfigItemNotFoundException("not found")

        client.cluster.get_config.side_effect = _get_config

        result = step.is_skip(Mock())

        assert result.result_type == ResultType.COMPLETED

    def test_not_skipped_when_migration_needed(self, step, old_tfhelper, client):
        """Migration proceeds when old plan has resources and new plan is empty."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
            "juju_application.cinder-volume-ceph",
        ]
        client.cluster.get_config.side_effect = ConfigItemNotFoundException("not found")

        result = step.is_skip(Mock())

        assert result.result_type == ResultType.COMPLETED

    def test_not_skipped_when_storage_config_empty(self, step, old_tfhelper, client):
        """Migration proceeds when storage config exists but has no entries."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]

        def _get_config(key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return '{"backends": {}, "cinder-volumes": {}}'
            if key == "StorageBackendsEnabled":
                return "[]"
            raise ConfigItemNotFoundException("not found")

        client.cluster.get_config.side_effect = _get_config

        result = step.is_skip(Mock())

        assert result.result_type == ResultType.COMPLETED


class TestMigrateCinderVolumeClearOldState:
    """Tests for _clear_old_state."""

    def test_removes_all_non_data_resources(self, step, old_tfhelper):
        """All non-data resources are removed from old state."""
        old_tfhelper.state_list.return_value = [
            "data.juju_model.machine_model",
            "juju_application.cinder-volume",
            "juju_application.cinder-volume-ceph",
            "juju_offer.storage-backend-offer",
            "juju_integration.cinder-volume-identity[0]",
            "juju_integration.cinder-volume-amqp[0]",
            "juju_integration.cinder-volume-database[0]",
            "juju_integration.cinder-volume-ceph-to-cinder-volume",
            "juju_integration.cinder-volume-ceph-to-ceph[0]",
        ]

        step._clear_old_state()

        # data source should NOT be removed
        expected_removals = [
            "juju_application.cinder-volume",
            "juju_application.cinder-volume-ceph",
            "juju_offer.storage-backend-offer",
            "juju_integration.cinder-volume-identity[0]",
            "juju_integration.cinder-volume-amqp[0]",
            "juju_integration.cinder-volume-database[0]",
            "juju_integration.cinder-volume-ceph-to-cinder-volume",
            "juju_integration.cinder-volume-ceph-to-ceph[0]",
        ]
        assert old_tfhelper.state_rm.call_count == len(expected_removals)
        for resource in expected_removals:
            old_tfhelper.state_rm.assert_any_call(resource)

    def test_data_sources_are_skipped(self, step, old_tfhelper):
        """Data sources are not removed from state."""
        old_tfhelper.state_list.return_value = [
            "data.juju_model.machine_model",
        ]

        step._clear_old_state()

        old_tfhelper.state_rm.assert_not_called()


class TestMigrateCinderVolumeRegisterBackend:
    """Tests for _register_internal_ceph_backend."""

    @patch(f"{_MODULE}.ceph_replica_scale", return_value=3)
    @patch(f"{_MODULE}.update_config")
    def test_registers_new_backend(self, mock_update_config, mock_scale, step, client):
        """Backend is registered via add_storage_backend when not present."""
        client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException("not found")
        )

        step._register_internal_ceph_backend("test-model-uuid")

        client.cluster.add_storage_backend.assert_called_once()
        call_kwargs = client.cluster.add_storage_backend.call_args[1]
        assert call_kwargs["name"] == INTERNAL_CEPH_BACKEND_NAME
        assert call_kwargs["backend_type"] == "internal-ceph"
        assert call_kwargs["model_uuid"] == "test-model-uuid"

    @patch(f"{_MODULE}.ceph_replica_scale", return_value=3)
    @patch(f"{_MODULE}.update_config")
    def test_updates_existing_backend(
        self, mock_update_config, mock_scale, step, client
    ):
        """Backend is updated via update_storage_backend when already present."""
        client.cluster.get_storage_backend.return_value = Mock()

        step._register_internal_ceph_backend("test-model-uuid")

        client.cluster.update_storage_backend.assert_called_once()
        client.cluster.add_storage_backend.assert_not_called()


class TestBackfillCephFeatureStateStep:
    """Tests for Ceph feature state backfill during refresh."""

    @patch(f"{_MODULE}.is_internal_ceph_enabled", return_value=False)
    def test_skips_when_internal_ceph_not_managed(
        self, _mock_is_internal_ceph_enabled, deployment, client
    ):
        step = BackfillCephFeatureStateStep(deployment, client)

        result = step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        deployment.get_feature_manager.return_value.resolve_feature.assert_not_called()

    @patch(f"{_MODULE}.write_ceph_config")
    @patch(f"{_MODULE}.load_ceph_config")
    @patch(f"{_MODULE}.is_internal_ceph_enabled", return_value=True)
    def test_backfills_ceph_feature_state(
        self,
        _mock_is_internal_ceph_enabled,
        mock_load_ceph_config,
        mock_write_ceph_config,
        deployment,
        client,
    ):
        from sunbeam.core.ceph import CephConfig

        mock_load_ceph_config.return_value = CephConfig(mode=None)
        feature = Mock(spec=EnableDisableFeature)
        deployment.get_feature_manager.return_value.resolve_feature.return_value = (
            feature
        )

        step = BackfillCephFeatureStateStep(deployment, client)
        result = step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        feature.update_feature_info.assert_called_once_with(
            client,
            {
                "enabled": "true",
                "default_storage_reconciled": "true",
            },
        )

    @patch(f"{_MODULE}.write_ceph_config")
    @patch(f"{_MODULE}.load_ceph_config")
    @patch(f"{_MODULE}.is_internal_ceph_enabled", return_value=True)
    def test_writes_ceph_mode_when_unset(
        self,
        _mock_is_internal_ceph_enabled,
        mock_load_ceph_config,
        mock_write_ceph_config,
        deployment,
        client,
    ):
        """An upgraded cluster with CephConfig.mode=None should get mode=MICROCEPH."""
        from sunbeam.core.ceph import CephConfig, CephDeploymentMode

        mock_load_ceph_config.return_value = CephConfig(mode=None)

        step = BackfillCephFeatureStateStep(deployment, client)
        step.run(Mock())

        mock_write_ceph_config.assert_called_once()
        written_client, written_config = mock_write_ceph_config.call_args.args
        assert written_client is client
        assert written_config.mode == CephDeploymentMode.MICROCEPH

    @patch(f"{_MODULE}.write_ceph_config")
    @patch(f"{_MODULE}.load_ceph_config")
    @patch(f"{_MODULE}.is_internal_ceph_enabled", return_value=True)
    def test_leaves_ceph_mode_when_already_set(
        self,
        _mock_is_internal_ceph_enabled,
        mock_load_ceph_config,
        mock_write_ceph_config,
        deployment,
        client,
    ):
        """Already-set CephConfig.mode must not be rewritten."""
        from sunbeam.core.ceph import CephConfig, CephDeploymentMode

        mock_load_ceph_config.return_value = CephConfig(
            mode=CephDeploymentMode.MICROCEPH
        )

        step = BackfillCephFeatureStateStep(deployment, client)
        step.run(Mock())

        mock_write_ceph_config.assert_not_called()


class TestImportCephResourcesToStorageFrameworkStep:
    """Tests for importing legacy Ceph resources into the storage plan."""

    @pytest.fixture
    def storage_tfhelper(self):
        helper = Mock()
        helper.state_list.return_value = []
        return helper

    @pytest.fixture
    def import_step(self, deployment, client, storage_tfhelper, jhelper):
        return ImportCephResourcesToStorageFrameworkStep(
            deployment=deployment,
            client=client,
            tfhelper=storage_tfhelper,
            jhelper=jhelper,
            model="machines",
        )

    @patch(f"{_MODULE}.read_config")
    def test_builds_imports_for_legacy_internal_ceph(
        self, mock_read_config, import_step, client, jhelper
    ):
        """Legacy cinder-volume resources are imported into new module addresses."""
        tfvars = {
            "cinder-volumes": {
                "cinder-volume": {
                    "application_name": "cinder-volume",
                    "keystone-offer-url": "admin/openstack.keystone",
                    "amqp-offer-url": "admin/openstack.rabbitmq",
                    "database-offer-url": "admin/openstack.cinder-db",
                    "cert-distributor-offer-url": None,
                }
            },
            "backends": {"internal-ceph": {}},
        }
        mock_read_config.return_value = {
            "juju_integration.cinder-volume-identity[0]": "legacy-id-identity",
            "juju_integration.cinder-volume-amqp[0]": "legacy-id-amqp",
            "juju_integration.cinder-volume-database[0]": "legacy-id-database",
        }

        imports = import_step._build_imports(
            tfvars,
            model_uuid="test-model-uuid",
            model_name="admin/openstack",
        )

        assert imports == [
            (
                'module.cinder-volume["cinder-volume"].juju_application.cinder-volume',
                "test-model-uuid:cinder-volume",
            ),
            (
                'module.cinder-volume["cinder-volume"].juju_offer.storage-backend-offer',
                "admin/openstack.cinder-volume",
            ),
            (
                'module.backends["internal-ceph"].juju_application.storage-backend',
                "test-model-uuid:cinder-volume-ceph",
            ),
            (
                'module.backends["internal-ceph"].juju_integration.storage-backend-to-cinder-volume',
                "test-model-uuid:cinder-volume:cinder-volume:cinder-volume-ceph:cinder-volume",
            ),
            (
                'module.backends["internal-ceph"].juju_integration.backend-extra-integration["microceph-ceph"]',
                "test-model-uuid:microceph:ceph:cinder-volume-ceph:ceph",
            ),
            (
                'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-identity[0]',
                "legacy-id-identity",
            ),
            (
                'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-amqp[0]',
                "legacy-id-amqp",
            ),
            (
                'module.cinder-volume["cinder-volume"].juju_integration.cinder-volume-database[0]',
                "legacy-id-database",
            ),
        ]

    @patch(f"{_MODULE}.read_config")
    def test_run_imports_missing_resources(
        self,
        mock_read_config,
        import_step,
        storage_tfhelper,
        jhelper,
    ):
        """Only missing resources are imported into the storage plan state."""

        def _read_config(_client, key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return {
                    "cinder-volumes": {
                        "cinder-volume": {
                            "application_name": "cinder-volume",
                            "keystone-offer-url": "admin/openstack.keystone",
                            "amqp-offer-url": "admin/openstack.rabbitmq",
                            "database-offer-url": "admin/openstack.cinder-db",
                            "cert-distributor-offer-url": None,
                        }
                    },
                    "backends": {"internal-ceph": {}},
                }
            if key == STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY:
                return {
                    "juju_integration.cinder-volume-identity[0]": "legacy-id-identity"
                }
            raise ConfigItemNotFoundException("not found")

        mock_read_config.side_effect = _read_config
        storage_tfhelper.state_list.return_value = [
            'module.cinder-volume["cinder-volume"].juju_application.cinder-volume'
        ]

        result = import_step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        storage_tfhelper.write_tfvars.assert_called_once()
        imported_addresses = [
            call.args[0] for call in storage_tfhelper.import_resource.call_args_list
        ]
        assert (
            'module.cinder-volume["cinder-volume"].juju_application.cinder-volume'
            not in imported_addresses
        )
        assert (
            'module.backends["internal-ceph"].juju_application.storage-backend'
            in imported_addresses
        )

    @patch(f"{_MODULE}.read_config")
    def test_run_treats_missing_state_file_as_empty_state(
        self,
        mock_read_config,
        import_step,
        storage_tfhelper,
    ):
        """A clean storage-backend plan should import from an empty initial state."""

        def _read_config(_client, key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return {
                    "cinder-volumes": {
                        "cinder-volume": {
                            "application_name": "cinder-volume",
                            "keystone-offer-url": "admin/openstack.keystone",
                            "amqp-offer-url": "admin/openstack.rabbitmq",
                            "database-offer-url": "admin/openstack.cinder-db",
                            "cert-distributor-offer-url": None,
                        }
                    },
                    "backends": {
                        "internal-ceph": {
                            "application_name": "cinder-volume-ceph",
                        }
                    },
                }
            raise ConfigItemNotFoundException("not found")

        mock_read_config.side_effect = _read_config
        storage_tfhelper.state_list.side_effect = TerraformException(
            "No state file was found!"
        )

        result = import_step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        storage_tfhelper.write_tfvars.assert_called_once()
        storage_tfhelper.import_resource.assert_called()

    @patch(f"{_MODULE}.read_config")
    def test_skip_when_internal_ceph_backend_missing(
        self,
        mock_read_config,
        import_step,
    ):
        """Nothing is imported when migration tfvars do not include internal-ceph."""
        mock_read_config.return_value = {
            "cinder-volumes": {
                "cinder-volume": {
                    "application_name": "cinder-volume",
                    "keystone-offer-url": "admin/openstack.keystone",
                    "amqp-offer-url": "admin/openstack.rabbitmq",
                    "database-offer-url": "admin/openstack.cinder-db",
                    "cert-distributor-offer-url": None,
                }
            },
            "backends": {},
        }

        result = import_step.is_skip(Mock())

        assert result.result_type == ResultType.SKIPPED


class TestMigrateCinderVolumeBuildTfvars:
    """Tests for _build_storage_tfvars."""

    def test_builds_correct_structure(self, step):
        """Built tfvars have the expected top-level keys and structure."""
        tfvars = step._build_storage_tfvars("test-model-uuid")

        assert tfvars["model"] == "test-model-uuid"
        assert "cinder-volume" in tfvars["cinder-volumes"]
        assert INTERNAL_CEPH_BACKEND_NAME in tfvars["backends"]

        cv_entry = tfvars["cinder-volumes"]["cinder-volume"]
        assert cv_entry["application_name"] == "cinder-volume"
        assert cv_entry["machine_ids"] == ["0", "1", "2"]
        assert tfvars["backends"][INTERNAL_CEPH_BACKEND_NAME]["application_name"] == (
            "cinder-volume-ceph"
        )
        assert tfvars["backends"][INTERNAL_CEPH_BACKEND_NAME]["units"] is None

    def test_normalizes_integer_machine_ids_to_strings(self, step, client):
        """Legacy integer machine IDs are normalized to the new tfvars contract."""
        client.cluster.list_nodes_by_role.return_value = [
            {"machineid": 0},
            {"machineid": 2},
            {"machineid": 1},
        ]

        tfvars = step._build_storage_tfvars("test-model-uuid")

        assert tfvars["cinder-volumes"]["cinder-volume"]["machine_ids"] == [
            "0",
            "1",
            "2",
        ]

    def test_includes_control_plane_offers(self, step):
        """Control plane offer URLs are included in cinder-volume entry."""
        tfvars = step._build_storage_tfvars("test-model-uuid")
        cv_entry = tfvars["cinder-volumes"]["cinder-volume"]

        assert cv_entry["keystone-offer-url"] == "admin/openstack.keystone"
        assert cv_entry["database-offer-url"] == "admin/openstack.cinder-db"
        assert cv_entry["amqp-offer-url"] == "admin/openstack.rabbitmq"

    def test_preserves_existing_backends_on_merge(self, step, client):
        """Existing third-party backends are preserved during migration."""
        pure_backend = {"charm_name": "cinder-purestorage", "units": 1}
        pure_principal = {
            "application_name": "cinder-volume",
            "machine_ids": ["0", "1"],
        }

        def _get_config(key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return json.dumps(
                    {
                        "model": "existing-uuid",
                        "backends": {"pure": pure_backend},
                        "cinder-volumes": {"cinder-volume": pure_principal},
                    }
                )
            if key == "StorageBackendsEnabled":
                return "[]"
            raise ConfigItemNotFoundException("not found")

        client.cluster.get_config.side_effect = _get_config

        tfvars = step._build_storage_tfvars("test-model-uuid")

        # Existing backend is preserved
        assert "pure" in tfvars["backends"]
        assert tfvars["backends"]["pure"] == pure_backend
        # Internal-ceph is added
        assert INTERNAL_CEPH_BACKEND_NAME in tfvars["backends"]
        # Existing HA principal is NOT overwritten
        assert tfvars["cinder-volumes"]["cinder-volume"] == pure_principal
        # Model preserved from existing config
        assert tfvars["model"] == "existing-uuid"

    def test_creates_principal_when_only_noha_exists(self, step, client):
        """HA principal is created when only non-HA exists (Hitachi scenario)."""
        noha_principal = {
            "application_name": "cinder-volume-noha",
            "machine_ids": ["0"],
        }

        def _get_config(key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return json.dumps(
                    {
                        "model": "existing-uuid",
                        "backends": {"hitachi": {"charm_name": "cinder-hitachi"}},
                        "cinder-volumes": {"cinder-volume-noha": noha_principal},
                    }
                )
            if key == "StorageBackendsEnabled":
                return "[]"
            raise ConfigItemNotFoundException("not found")

        client.cluster.get_config.side_effect = _get_config

        tfvars = step._build_storage_tfvars("test-model-uuid")

        # Existing non-HA principal is preserved
        assert "cinder-volume-noha" in tfvars["cinder-volumes"]
        assert tfvars["cinder-volumes"]["cinder-volume-noha"] == noha_principal
        # HA principal is created for internal-ceph
        assert "cinder-volume" in tfvars["cinder-volumes"]
        assert tfvars["cinder-volumes"]["cinder-volume"]["machine_ids"] == [
            "0",
            "1",
            "2",
        ]
        # Both backends present
        assert "hitachi" in tfvars["backends"]
        assert INTERNAL_CEPH_BACKEND_NAME in tfvars["backends"]


class TestMigrateCinderVolumeRun:
    """Tests for run method."""

    @patch(f"{_MODULE}.update_config")
    def test_run_succeeds(self, mock_update_config, step, old_tfhelper, client):
        """Migration run completes successfully."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
            "juju_application.cinder-volume-ceph",
        ]
        old_tfhelper.pull_state.return_value = {
            "resources": [
                {
                    "mode": "managed",
                    "type": "juju_application",
                    "name": "cinder-volume",
                    "instances": [{"attributes": {"id": "legacy-app-id"}}],
                }
            ]
        }
        client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException("not found")
        )

        result = step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        # Old state was cleared
        assert old_tfhelper.state_rm.call_count == 2
        # Backend was registered
        client.cluster.add_storage_backend.assert_called_once()
        # Tfvars were saved
        mock_update_config.assert_called()
        mock_update_config.assert_any_call(
            client,
            STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY,
            {"juju_application.cinder-volume": "legacy-app-id"},
        )

    def test_run_fails_on_state_clear_error(self, step, old_tfhelper):
        """Run fails when clearing old state raises TerraformException."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]
        old_tfhelper.state_rm.side_effect = TerraformException("state rm failed")

        result = step.run(Mock())

        assert result.result_type == ResultType.FAILED
        assert "terraform state" in result.message.lower()

    @patch(f"{_MODULE}.update_config")
    def test_run_fails_on_backend_registration_error(
        self, mock_update_config, step, old_tfhelper, client
    ):
        """Run fails when backend registration raises an exception."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]
        client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException("not found")
        )
        client.cluster.add_storage_backend.side_effect = RuntimeError("api error")

        result = step.run(Mock())

        assert result.result_type == ResultType.FAILED
        assert "internal-ceph" in result.message.lower()

    @patch(f"{_MODULE}.update_config")
    def test_run_does_not_clear_old_state_when_registration_fails(
        self, mock_update_config, step, old_tfhelper, client
    ):
        """Partial failure leaves old state intact for safe retry.

        Regression test for a retry wedge where state was cleared first:
        on retry, is_skip saw no resources and returned SKIPPED, leaving
        the cluster half-migrated.
        """
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]
        client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException("not found")
        )
        client.cluster.add_storage_backend.side_effect = RuntimeError("api error")

        result = step.run(Mock())

        assert result.result_type == ResultType.FAILED
        # The old state must remain intact so the retry can re-run.
        old_tfhelper.state_rm.assert_not_called()

    @patch(f"{_MODULE}.update_config")
    def test_run_clears_old_state_only_after_clusterd_writes_succeed(
        self, mock_update_config, step, old_tfhelper, client
    ):
        """Ordering invariant: old state is cleared last."""
        old_tfhelper.state_list.return_value = [
            "juju_application.cinder-volume",
        ]
        old_tfhelper.pull_state.return_value = {"resources": []}
        client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException("not found")
        )

        call_order: list[str] = []

        def _track_add_storage(**kwargs):
            call_order.append("register")

        def _track_state_rm(resource):
            call_order.append("state_rm")

        client.cluster.add_storage_backend.side_effect = _track_add_storage
        old_tfhelper.state_rm.side_effect = _track_state_rm

        def _track_update_config(client_arg, key, value):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                call_order.append("build_tfvars")

        mock_update_config.side_effect = _track_update_config

        result = step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        # register happens before build, build before state_rm
        assert call_order.index("register") < call_order.index("build_tfvars")
        assert call_order.index("build_tfvars") < call_order.index("state_rm")


class TestImportCephResourcesCleanup:
    """Tests for legacy-import-id cleanup after successful import."""

    @pytest.fixture
    def storage_tfhelper(self):
        helper = Mock()
        helper.state_list.return_value = []
        return helper

    @pytest.fixture
    def import_step(self, deployment, client, storage_tfhelper, jhelper):
        return ImportCephResourcesToStorageFrameworkStep(
            deployment=deployment,
            client=client,
            tfhelper=storage_tfhelper,
            jhelper=jhelper,
            model="machines",
        )

    @patch(f"{_MODULE}.read_config")
    def test_run_clears_legacy_import_ids_after_success(
        self,
        mock_read_config,
        import_step,
        client,
        storage_tfhelper,
    ):
        """The legacy-id map must be removed from clusterd once imports succeed."""

        def _read_config(_client, key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return {
                    "cinder-volumes": {
                        "cinder-volume": {
                            "application_name": "cinder-volume",
                            "keystone-offer-url": "admin/openstack.keystone",
                            "amqp-offer-url": "admin/openstack.rabbitmq",
                            "database-offer-url": "admin/openstack.cinder-db",
                            "cert-distributor-offer-url": None,
                        }
                    },
                    "backends": {"internal-ceph": {}},
                }
            if key == STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY:
                return {
                    "juju_integration.cinder-volume-identity[0]": "legacy-id-identity"
                }
            raise ConfigItemNotFoundException("not found")

        mock_read_config.side_effect = _read_config

        result = import_step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
        client.cluster.delete_config.assert_called_once_with(
            STORAGE_BACKEND_LEGACY_IMPORT_IDS_KEY
        )

    @patch(f"{_MODULE}.read_config")
    def test_run_tolerates_missing_legacy_import_ids_key_on_cleanup(
        self,
        mock_read_config,
        import_step,
        client,
        storage_tfhelper,
    ):
        """Cleanup must not fail if the legacy-id key is already gone."""

        def _read_config(_client, key):
            if key == STORAGE_BACKEND_TFVAR_CONFIG_KEY:
                return {
                    "cinder-volumes": {
                        "cinder-volume": {
                            "application_name": "cinder-volume",
                            "keystone-offer-url": "admin/openstack.keystone",
                            "amqp-offer-url": "admin/openstack.rabbitmq",
                            "database-offer-url": "admin/openstack.cinder-db",
                            "cert-distributor-offer-url": None,
                        }
                    },
                    "backends": {"internal-ceph": {}},
                }
            raise ConfigItemNotFoundException("not found")

        mock_read_config.side_effect = _read_config
        client.cluster.delete_config.side_effect = ConfigItemNotFoundException(
            "not found"
        )

        result = import_step.run(Mock())

        assert result.result_type == ResultType.COMPLETED
