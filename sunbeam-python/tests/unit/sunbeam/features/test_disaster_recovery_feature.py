# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.core.manifest import FeatureConfig, TerraformManifest
from sunbeam.features.disaster_recovery.feature import (
    DisasterRecoveryFeature,
    DisasterRecoveryFeatureConfig,
    S3Integration,
)
from sunbeam.features.interface.v1.openstack import (
    DatabaseTopology,
    TerraformPlanLocation,
)
from sunbeam.steps.backup_restore import S3_ENDPOINT


class TestDisasterRecoveryFeature:
    def test_feature_metadata(self):
        feature = DisasterRecoveryFeature()

        assert feature.name == "disaster-recovery"
        assert feature.generally_available is False
        assert feature.tf_plan_location == TerraformPlanLocation.FEATURE_REPO

    def test_set_application_names_is_per_target_app(self):
        feature = DisasterRecoveryFeature()
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
        feature = DisasterRecoveryFeature()
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
        feature = DisasterRecoveryFeature()
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
        feature.get_database_topology = Mock(return_value=DatabaseTopology.SINGLE)

        assert feature.set_application_names(deployment) == [
            "mysql-s3-integrator",
            "vault-s3-integrator",
        ]

    def test_multi_database_topology_uses_only_service_mysql(self):
        feature = DisasterRecoveryFeature()
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
        feature.get_database_topology = Mock(return_value=DatabaseTopology.MULTI)

        assert feature.set_application_names(deployment) == [
            "keystone-s3-integrator",
            "vault-s3-integrator",
        ]

    def test_enable_disable_tfvars(self):
        feature = DisasterRecoveryFeature()
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

        assert feature.set_tfvars_on_enable(deployment, FeatureConfig()) == {
            "enable-disaster-recovery": True,
            "openstack-model-uuid": "openstack-uuid",
            "s3-integrator-config": {},
            "s3-integrator-secret-data": {},
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
        assert feature.set_tfvars_on_disable(deployment) == {
            "enable-disaster-recovery": False,
            "openstack-model-uuid": "openstack-uuid",
            "s3-integrator-config": {},
            "s3-integrator-secret-data": {},
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
        feature = DisasterRecoveryFeature()
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

        config = DisasterRecoveryFeatureConfig(
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
                "path": "backups/keystone-mysql",
                "region": "us-east-2",
                "endpoint": "https://s3.us-east-2.amazonaws.com",
            },
            "vault-s3-integrator": {
                "bucket": "openstack-backups",
                "path": "backups/vault",
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
        feature = DisasterRecoveryFeature()
        deployment = Mock()
        feature._s3_integrations = Mock(return_value=[])
        feature._ask_prompt = Mock()
        feature._ask_password = Mock()
        config = feature._prompt_s3_configuration(deployment, show_hints=False)

        feature._ask_prompt.assert_not_called()
        feature._ask_password.assert_not_called()
        assert isinstance(config, DisasterRecoveryFeatureConfig)
        assert config.configure_s3_integrators is False

    def test_prompt_s3_configuration_accepted_returns_config(self):
        feature = DisasterRecoveryFeature()
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
        feature.enable_feature = Mock()

        from sunbeam.features.disaster_recovery import feature as dr_feature

        confirm_mock = Mock()
        confirm_mock.ask.return_value = True
        with patch.object(dr_feature, "ConfirmQuestion", return_value=confirm_mock):
            config = feature._prompt_s3_configuration(deployment, show_hints=True)

        assert isinstance(config, DisasterRecoveryFeatureConfig)
        assert config.configure_s3_integrators is True
        assert config.bucket == "openstack-backups"
        assert config.path == "backups"
        assert config.region == "us-east-2"
        assert config.endpoint == "https://s3.us-east-2.amazonaws.com"
        assert config.access_key == "secret"
        assert config.secret_key == "secret"

    def test_validate_prompted_s3_config_requires_bucket_access_secret(self):
        feature = DisasterRecoveryFeature()

        bad_bucket = DisasterRecoveryFeatureConfig(
            configure_s3_integrators=True,
            bucket="",
            access_key="AKIA",
            secret_key="secret",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_bucket)

        bad_access = DisasterRecoveryFeatureConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="",
            secret_key="secret",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_access)

        bad_secret = DisasterRecoveryFeatureConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="AKIA",
            secret_key="",
            endpoint="https://s3.us-east-2.amazonaws.com",
        )
        with pytest.raises(click.ClickException):
            feature._validate_s3_config(bad_secret)

    def test_validate_prompted_s3_config_rejects_bad_endpoint(self):
        feature = DisasterRecoveryFeature()
        config = DisasterRecoveryFeatureConfig(
            configure_s3_integrators=True,
            bucket="bucket",
            access_key="AKIA",
            secret_key="secret",
            endpoint="s3.us-east-2.amazonaws.com",
        )

        with pytest.raises(click.ClickException):
            feature._validate_s3_config(config)

    def test_integrator_app_name_uses_service_prefix_for_mysql(self):
        feature = DisasterRecoveryFeature()

        assert (
            feature._s3_integrator_app_name("keystone-mysql")
            == "keystone-s3-integrator"
        )
        assert feature._s3_integrator_app_name("vault") == "vault-s3-integrator"

    def test_default_software_overrides_includes_terraform_plan(self):
        feature = DisasterRecoveryFeature()

        software = feature.default_software_overrides()

        assert feature.tfplan in software.terraform
        assert isinstance(software.terraform[feature.tfplan], TerraformManifest)
        assert (
            software.terraform[feature.tfplan].source.name == "deploy-disaster-recovery"
        )

    def test_set_application_status_overlay_on_enable_accepts_blocked(self):
        feature = DisasterRecoveryFeature()
        deployment = Mock()
        feature.set_application_names = Mock(return_value=["keystone-s3-integrator"])

        overlay = feature.get_app_status_overlay_on_enable(deployment)

        assert overlay == {"keystone-s3-integrator": {"status": ["active", "blocked"]}}

    def test_manifest_attributes_tfvar_map_includes_s3_integrator(self):
        feature = DisasterRecoveryFeature()

        tfvar_map = feature.manifest_attributes_tfvar_map()

        assert feature.tfplan in tfvar_map
        charm_map = tfvar_map[feature.tfplan]["charms"]
        assert "s3-integrator" in charm_map
        assert charm_map["s3-integrator"]["channel"] == "s3-integrator-channel"
        assert charm_map["s3-integrator"]["revision"] == "s3-integrator-revision"
        assert charm_map["s3-integrator"]["config"] == "s3-integrator-config"

    def test_discover_relation_targets_mysql_and_vault_only(self):
        feature = DisasterRecoveryFeature()
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
        feature = DisasterRecoveryFeature()
        apps = {
            "mystery-app": Mock(),
            "vault": Mock(charm_name="vault-k8s"),
        }

        assert feature._s3_discover_relation_targets(apps) == ["vault"]

    def test_discover_relation_targets_uses_components_with_s3_validation(self):
        feature = DisasterRecoveryFeature()
        apps = {
            "mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
            "random-app": Mock(charm_name="random-charm"),
        }

        assert feature._s3_discover_relation_targets(apps) == ["mysql", "vault"]

    def test_build_s3_integrations_map(self):
        feature = DisasterRecoveryFeature()
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

    def test_build_s3_integrations_skips_app_with_non_dr_s3_relation(self):
        feature = DisasterRecoveryFeature()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {"3": "legacy-dr-integrator"}

        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_skips_preexisting_unmanaged_integrator(self):
        feature = DisasterRecoveryFeature()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "keystone-s3-integrator": Mock(charm_name="s3-integrator"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {}

        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_keeps_dr_owned_relation(self):
        feature = DisasterRecoveryFeature()
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
        feature = DisasterRecoveryFeature()
        apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "keystone-s3-integrator": Mock(charm_name="s3-integrator"),
        }
        jhelper = Mock()
        jhelper.get_relation_map.return_value = {"3": "keystone-s3-integrator"}

        # No managed map: pre-existing integrator with the conventional name
        # must not be claimed as DR-owned.
        assert feature._s3_build_integrations(jhelper, apps) == []

    def test_build_s3_integrations_without_relations_does_not_skip(self):
        feature = DisasterRecoveryFeature()
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
