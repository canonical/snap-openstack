# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core.manifest import FeatureConfig, TerraformManifest
from sunbeam.features.disaster_recovery.feature import (
    DisasterRecoveryFeature,
    S3Integration,
)
from sunbeam.features.interface.v1.openstack import TerraformPlanLocation
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
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
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
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
        jhelper = deployment.get_juju_helper.return_value
        status = Mock()
        status.apps = {
            "keystone-mysql": Mock(charm_name="mysql-k8s"),
            "vault": Mock(charm_name="vault-k8s"),
        }
        jhelper.get_model_status.return_value = status
        jhelper.get_relation_map.side_effect = [{"3": "legacy-dr-integrator"}, {}]

        assert feature.set_application_names(deployment) == ["vault-s3-integrator"]

    def test_enable_disable_tfvars(self):
        feature = DisasterRecoveryFeature()
        deployment = Mock()
        deployment.get_client.return_value.cluster.get_config.return_value = "{}"
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
