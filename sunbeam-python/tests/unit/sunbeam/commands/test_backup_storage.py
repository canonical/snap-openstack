# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.commands.backup_storage import (
    BackupStorage,
    BackupStorageConfig,
    S3Integration,
)
from sunbeam.core.manifest import Manifest
from sunbeam.steps.backup_restore import S3_ENDPOINT
from sunbeam.versions import MANIFEST_ATTRIBUTES_TFVAR_MAP, TERRAFORM_DIR_NAMES


class TestBackupStorage:
    @pytest.mark.parametrize(
        ("path", "expected_path"),
        [
            ("/", "/mysql"),
            ("/backups", "/backups/mysql"),
            ("backups", "/backups/mysql"),
        ],
    )
    def test_s3_integrator_config_normalizes_mysql_paths(self, path, expected_path):
        feature = BackupStorage()
        config = BackupStorageConfig(
            configure_managed_s3_integrators=True,
            path=path,
        )
        integrations = [
            S3Integration(
                app_name="mysql",
                integrator_app="mysql-s3-integrator",
                target_endpoint=S3_ENDPOINT,
            )
        ]

        tfvar = feature._s3_integrator_config_tfvar(config, integrations)

        assert tfvar["mysql-s3-integrator"]["path"] == expected_path

    def test_core_plan_metadata(self):
        assert TERRAFORM_DIR_NAMES["backup-storage-plan"] == "deploy-backup-storage"
        assert "s3-integrator" in Manifest().core.software.charms

    def test_set_application_names_is_per_target_app(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = (
            '{"database": "multi"}'
        )
        jhelper = deployment.get_juju_helper.return_value
        status = Mock()
        status.apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
            "nova": Mock(charm_name="nova-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.return_value = {}

        assert feature.set_application_names(deployment) == [
            "keystone-s3-integrator",
            "vault-s3-integrator",
        ]

    def test_set_application_names_skips_existing_s3_relation(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = (
            '{"database": "multi"}'
        )
        jhelper = deployment.get_juju_helper.return_value
        status = Mock()
        status.apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.side_effect = [{"3": "legacy-dr-integrator"}, {}]

        assert feature.set_application_names(deployment) == ["vault-s3-integrator"]

    def test_single_database_topology_uses_only_shared_mysql(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
        jhelper = deployment.get_juju_helper.return_value
        status = Mock()
        status.apps = {
            "mysql": Mock(charm_name="mysql-k8s"),
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.return_value = {}
        feature.get_database_topology = Mock(return_value="single")

        assert feature.set_application_names(deployment) == [
            "mysql-s3-integrator",
            "vault-s3-integrator",
        ]

    def test_multi_database_topology_uses_only_service_mysql(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
        jhelper = deployment.get_juju_helper.return_value
        status = Mock()
        status.apps = {
            "mysql": Mock(charm_name="mysql-k8s"),
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.return_value = {}
        feature.get_database_topology = Mock(return_value="multi")

        assert feature.set_application_names(deployment) == [
            "keystone-s3-integrator",
            "vault-s3-integrator",
        ]

    def test_setup_tfvars(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = (
            '{"database": "multi"}'
        )
        jhelper = deployment.get_juju_helper.return_value
        jhelper.get_model_uuid.return_value = "openstack-uuid"
        status = Mock()
        status.apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.return_value = {}

        assert feature.set_tfvars_on_enable(deployment, BackupStorageConfig()) == {
            "enable-backup-storage": True,
            "openstack-model-uuid": "openstack-uuid",
            "s3-integrator-apps": [
                "keystone-s3-integrator",
                "vault-s3-integrator",
            ],
            "s3-integrations": {
                "keystone-mysql": {
                    "integrator_app": "keystone-s3-integrator",
                    "target_endpoint": S3_ENDPOINT,
                },
                "vault": {
                    "integrator_app": "vault-s3-integrator",
                    "target_endpoint": S3_ENDPOINT,
                },
            },
        }

    def test_set_tfvars_on_enable_includes_per_app_s3_config(self):
        feature = BackupStorage()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = (
            '{"database": "multi"}'
        )
        jhelper = deployment.get_juju_helper.return_value
        jhelper.get_model_uuid.return_value = "openstack-uuid"
        status = Mock()
        status.apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.return_value = {}

        config = BackupStorageConfig(
            configure_managed_s3_integrators=True,
            bucket="openstack-backups",
            path="backups",
            region="us-east-2",
            endpoint="https://s3.us-east-2.amazonaws.com",
            access_key="AKIA...",
            secret_key="secret",
        )

        tfvars = feature.set_tfvars_on_enable(deployment, config)

        assert tfvars["s3-integrator-config"] == {
            "keystone-s3-integrator": {
                "bucket": "openstack-backups",
                "path": "/backups/keystone-mysql",
                "region": "us-east-2",
                "endpoint": "https://s3.us-east-2.amazonaws.com",
            },
            "vault-s3-integrator": {
                "bucket": "openstack-backups",
                "path": "/backups/vault",
                "region": "us-east-2",
                "endpoint": "https://s3.us-east-2.amazonaws.com",
            },
        }
        assert tfvars["s3-integrator-secret-data"] == {
            "keystone-s3-integrator": {
                "access-key": "AKIA...",
                "secret-key": "secret",
            },
            "vault-s3-integrator": {
                "access-key": "AKIA...",
                "secret-key": "secret",
            },
        }

    def test_prompt_s3_configuration_without_targets_skips_prompt(self):
        feature = BackupStorage()
        deployment = Mock()
        feature._s3_integrations = Mock(return_value=[])
        feature._ask_prompt = Mock()
        feature._ask_password = Mock()
        config = feature._prompt_s3_configuration(deployment, show_hints=False)

        feature._ask_prompt.assert_not_called()
        feature._ask_password.assert_not_called()
        assert isinstance(config, BackupStorageConfig)
        assert config.configure_s3_integrators is False

    def test_prompt_s3_configuration_accepted_returns_config(self):
        feature = BackupStorage()
        deployment = Mock()
        feature._s3_integrations = Mock(
            return_value=[
                S3Integration(
                    app_name="keystone-mysql",
                    integrator_app="keystone-s3-integrator",
                    target_endpoint=S3_ENDPOINT,
                )
            ]
        )
        feature._ask_prompt = Mock(
            side_effect=[
                "openstack-backups",
                "backups",
                "us-east-2",
                "https://s3.us-east-2.amazonaws.com",
            ]
        )
        feature._ask_password = Mock(return_value="secret")
        feature._validate_s3_config = Mock()
        from sunbeam.commands import backup_storage

        confirm_mock = Mock()
        confirm_mock.ask.return_value = True
        with patch.object(backup_storage, "ConfirmQuestion", return_value=confirm_mock):
            config = feature._prompt_s3_configuration(deployment, show_hints=True)

        assert isinstance(config, BackupStorageConfig)
        assert config.configure_s3_integrators is True
        assert config.bucket == "openstack-backups"
        assert config.path == "backups"
        assert config.region == "us-east-2"
        assert config.endpoint == "https://s3.us-east-2.amazonaws.com"
        assert config.access_key == "secret"
        assert config.secret_key == "secret"

    def test_validate_prompted_s3_config_requires_bucket_access_secret(self):
        feature = BackupStorage()

        bad_bucket = BackupStorageConfig(
            configure_s3_integrators=True,
            bucket="",
            access_key="AKIA",
            secret_key="secret",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_bucket)

        bad_access = BackupStorageConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="",
            secret_key="secret",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_access)

        bad_secret = BackupStorageConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="AKIA",
            secret_key="",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_secret)

    def test_validate_prompted_s3_config_rejects_bad_endpoint(self):
        feature = BackupStorage()
        config = BackupStorageConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="AKIA",
            secret_key="secret",
            endpoint="s3.us-east-2.amazonaws.com",
        )

        with pytest.raises(click.ClickException):
            feature._validate_s3_config(config)

    def test_integrator_app_name_uses_service_prefix_for_mysql(self):
        feature = BackupStorage()

        assert (
            feature._s3_integrator_app_name("keystone-mysql")
            == "keystone-s3-integrator"
        )
        assert feature._s3_integrator_app_name("vault") == "vault-s3-integrator"

    def test_core_software_includes_terraform_plan(self):
        software = Manifest().core.software
        assert (
            software.terraform["backup-storage-plan"].source.name
            == "deploy-backup-storage"
        )

    def test_apply_accepts_blocked_integrators(self):
        from sunbeam.commands.backup_storage import ApplyBackupStorageStep
        from sunbeam.core.common import ResultType

        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
        step = ApplyBackupStorageStep(
            deployment,
            Mock(),
            {
                "s3-integrations": {
                    "keystone-mysql": {"integrator_app": "keystone-s3-integrator"}
                }
            },
        )
        assert step.run(Mock()).result_type == ResultType.COMPLETED
        assert (
            deployment.get_juju_helper.return_value.wait_until_desired_status.call_args.kwargs[
                "status"
            ]
            == ["active", "blocked"]
        )

    def test_manifest_attributes_tfvar_map_includes_s3_integrator(self):
        charm_map = MANIFEST_ATTRIBUTES_TFVAR_MAP["backup-storage-plan"]["charms"]
        assert "s3-integrator" in charm_map
        assert charm_map["s3-integrator"]["channel"] == "s3-integrator-channel"
        assert charm_map["s3-integrator"]["revision"] == "s3-integrator-revision"
        assert "config" not in charm_map["s3-integrator"]

    def test_discover_relation_targets_mysql_and_vault_only(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
            "nova": Mock(charm_name="nova-k8s"),
            "glance": Mock(charm_name="glance-k8s"),
        }

        assert feature._s3_discover_relation_targets(apps) == [
            "keystone-mysql",
            "vault",
        ]

    def test_discover_relation_targets_ignores_missing_charm_name(self):
        feature = BackupStorage()
        apps = {
            "mystery-app": Mock(),
            "vault": Mock(charm_name="vault-k8s"),
        }

        assert feature._s3_discover_relation_targets(apps) == ["vault"]

    def test_discover_relation_targets_uses_components_with_s3_validation(self):
        feature = BackupStorage()
        apps = {
            "mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
            "random-app": Mock(charm_name="random-charm"),
        }

        assert feature._s3_discover_relation_targets(apps) == ["mysql", "vault"]

    def test_build_s3_integrations_map(self):
        feature = BackupStorage()
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {}
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
            "nova": Mock(charm_name="nova-k8s"),
        }

        assert feature._s3_build_integrations(jhelper, apps) == [
            S3Integration(
                app_name="keystone-mysql",
                integrator_app="keystone-s3-integrator",
                target_endpoint=S3_ENDPOINT,
            ),
            S3Integration(
                app_name="vault",
                integrator_app="vault-s3-integrator",
                target_endpoint=S3_ENDPOINT,
            ),
        ]

    def test_build_s3_integrations_skips_app_with_external_s3_relation(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {"3": "legacy-dr-integrator"}

        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_skips_preexisting_unmanaged_integrator(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "keystone-s3-integrator": Mock(charm_name="s3-integrator"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {}

        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_keeps_owned_relation(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "keystone-s3-integrator": Mock(charm_name="s3-integrator"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {"3": "keystone-s3-integrator"}
        managed = {"keystone-mysql": "keystone-s3-integrator"}

        assert feature._s3_build_integrations(jhelper, apps, managed) == [
            S3Integration(
                app_name="keystone-mysql",
                integrator_app="keystone-s3-integrator",
                target_endpoint=S3_ENDPOINT,
            )
        ]

    def test_build_s3_integrations_skips_unmanaged_relation_matching_name(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "keystone-s3-integrator": Mock(charm_name="s3-integrator"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {"3": "keystone-s3-integrator"}

        # No managed map: pre-existing integrator with the conventional name
        # must not be claimed as Sunbeam-owned.
        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_without_relations_does_not_skip(self):
        feature = BackupStorage()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {}

        assert feature._s3_build_integrations(jhelper, apps) == [
            S3Integration(
                app_name="keystone-mysql",
                integrator_app="keystone-s3-integrator",
                target_endpoint=S3_ENDPOINT,
            )
        ]
