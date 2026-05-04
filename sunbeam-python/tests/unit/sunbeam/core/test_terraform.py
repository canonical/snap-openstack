# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import functools
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

import sunbeam.core.deployment as deployment_mod
import sunbeam.core.manifest as manifest_mod
import sunbeam.core.terraform as terraform_mod
from sunbeam.core.deployment import Deployment
from sunbeam.core.progress import NoOpReporter
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.versions import OPENSTACK_CHANNEL

test_manifest = """
core:
  software:
    juju:
      bootstrap_args:
        - --agent-version=3.2.4
    charms:
      keystone-k8s:
        channel: 2023.1/stable
        revision: 234
        config:
          debug: True
      glance-k8s:
        channel: 2023.1/stable
        revision: 134
        config:
          ceph-osd-replication-count: 5
    terraform:
      openstack-plan:
        source: /home/ubuntu/openstack-tf
      hypervisor-plan:
        source: /home/ubuntu/hypervisor-tf
"""

test_manifest_with_rabbitmq_storage = """
core:
  software:
    charms:
      rabbitmq-k8s:
        storage:
          rabbitmq-data: 4G
"""

test_manifest_with_traefik_config_map = """
core:
  software:
    charms:
      traefik-k8s:
        config-map:
          traefik:
            tls-ca: dGVzdC1jYQ==
          traefik-public:
            tls-ca: dGVzdC1jYQ==
          traefik-rgw:
            tls-ca: dGVzdC1jYQ==
"""


@pytest.fixture()
def deployment():
    with patch("sunbeam.core.deployment.Deployment") as p:
        dep = p(name="", url="", type="")
        dep.get_manifest.side_effect = functools.partial(Deployment.get_manifest, dep)
        dep.get_tfhelper.side_effect = functools.partial(Deployment.get_tfhelper, dep)
        dep.parse_manifest.side_effect = functools.partial(
            Deployment.parse_manifest, dep
        )
        dep._load_tfhelpers.side_effect = functools.partial(
            Deployment._load_tfhelpers, dep
        )
        dep.__setattr__("_tfhelpers", {})
        dep._manifest = None
        dep.__setattr__("name", "test_deployment")
        dep.get_feature_manager.return_value = Mock(
            get_all_feature_manifests=Mock(return_value={}),
            get_all_feature_manifest_tfvar_map=Mock(return_value={}),
        )
        yield dep


@pytest.fixture()
def read_config():
    with patch("sunbeam.core.terraform.read_config") as p:
        yield p


class TestTerraformHelper:
    def test_update_tfvars_and_apply_tf(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        tfplan = "openstack-plan"
        extra_tfvars = {
            "ldap-apps": {"dom2": {"domain-name": "dom2"}},
            "glance-revision": 555,
            "glance-config": {"ceph-osd-replication-count": 3},
        }
        read_config.return_value = {
            "keystone-channel": OPENSTACK_CHANNEL,
            "neutron-channel": "2023.1/stable",
            "neutron-revision": 123,
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            "mysql-config": {"debug": True},
        }
        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply") as apply,
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", extra_tfvars
            )
            write_tfvars.assert_called_once()
            apply.assert_called_once()
            applied_tfvars = write_tfvars.call_args.args[0]

        # Assert values coming from manifest and not in config db
        assert applied_tfvars.get("glance-channel") == "2023.1/stable"

        # Assert values coming from manifest and in config db
        assert applied_tfvars.get("keystone-channel") == "2023.1/stable"
        assert applied_tfvars.get("keystone-revision") == 234
        assert applied_tfvars.get("keystone-config") == {"debug": True}

        # Assert values coming from default not in config db
        assert applied_tfvars.get("nova-channel") == OPENSTACK_CHANNEL

        # Assert values coming from default and in config db
        assert applied_tfvars.get("neutron-channel") == OPENSTACK_CHANNEL

        # Assert values coming from extra_tfvars and in config db
        assert applied_tfvars.get("ldap-apps") == extra_tfvars.get("ldap-apps")

        # Assert values coming from extra_tfvars and in manifest
        assert applied_tfvars.get("glance-revision") == 555

        # Assert remove keys from read_config if not present in manifest+defaults
        # or override
        assert "neutron-revision" not in applied_tfvars.keys()

        # Below are asserts for charm config parameters
        # Assert config values coming from extra_tfvars and in manifest
        # For charm configs (dicts): manifest has precedence over override
        assert applied_tfvars.get("glance-config") == {"ceph-osd-replication-count": 5}

        # While mysql-config is not in the manifest, it is in config db,
        # and mysql-config is one of the preserve vars of the OpenStack plan
        assert applied_tfvars.get("mysql-config") == {"debug": True}

    def test_source_tracking_computed_keys_preserved(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test that computed keys are tracked and persist across runs."""
        tfplan = "openstack-plan"
        # First run: override_tfvars provides computed values
        extra_tfvars = {
            "vault-config": {"common_name": "example.com"},
            "ha-scale": 3,
        }
        read_config.return_value = {}

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars"),
            patch.object(tfhelper, "apply"),
            patch("sunbeam.core.terraform.update_config") as update_config,
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", extra_tfvars
            )

            # Verify computed keys were tracked
            saved_data = update_config.call_args.args[2]
            assert "_computed_keys" in saved_data
            assert "vault-config" in saved_data["_computed_keys"]
            assert "ha-scale" in saved_data["_computed_keys"]

    def test_source_tracking_manifest_overrides_computed_for_charm_configs(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test that manifest wins over override for charm config conflicts."""
        tfplan = "openstack-plan"
        # DB has computed config
        read_config.return_value = {
            "_computed_keys": ["glance-config"],
            "glance-config": {"ceph-osd-replication-count": 3, "computed_field": "v1"},
        }
        # Override provides conflicting value
        extra_tfvars = {
            "glance-config": {"ceph-osd-replication-count": 7, "override_field": "v2"},
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", extra_tfvars
            )
            applied_tfvars = write_tfvars.call_args.args[0]

            # Manifest value (5) should win over override (7)
            # But other fields should be merged
            assert applied_tfvars["glance-config"]["ceph-osd-replication-count"] == 5
            # Fields from computed DB should be preserved
            assert applied_tfvars["glance-config"]["computed_field"] == "v1"
            # Fields from override should be included
            assert applied_tfvars["glance-config"]["override_field"] == "v2"

    def test_source_tracking_migration_from_preserve_list(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test migration from old preserve list to new source tracking."""
        tfplan = "openstack-plan"
        # Old DB without _computed_keys (mysql-config is in preserve list)
        read_config.return_value = {
            "mysql-config": {"debug": True},
            "keystone-channel": OPENSTACK_CHANNEL,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
            patch("sunbeam.core.terraform.update_config") as update_config,
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)

            applied_tfvars = write_tfvars.call_args.args[0]
            # mysql-config should be preserved (from preserve list)
            assert applied_tfvars.get("mysql-config") == {"debug": True}

            # Verify _computed_keys now includes preserve list
            saved_data = update_config.call_args.args[2]
            assert "_computed_keys" in saved_data
            # Should include items from preserve list
            assert any(
                key in saved_data["_computed_keys"]
                for key in ["mysql-config", "mysql-config-map"]
            )

    def test_traefik_config_map_from_manifest(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test that traefik-config-map is read from manifest and passed as tfvar."""
        tfplan = "openstack-plan"
        read_config.return_value = {}

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {
            "data": test_manifest_with_traefik_config_map
        }
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            # traefik-config-map should be populated from manifest config-map field
            assert applied_tfvars.get("traefik-config-map") == {
                "traefik": {"tls-ca": "dGVzdC1jYQ=="},
                "traefik-public": {"tls-ca": "dGVzdC1jYQ=="},
                "traefik-rgw": {"tls-ca": "dGVzdC1jYQ=="},
            }

    def test_traefik_config_map_removed_when_not_in_manifest(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test traefik-config-map is removed from tfvars when absent from manifest."""
        tfplan = "openstack-plan"
        # DB has old traefik-config-map value (not computed, manifest-derived)
        read_config.return_value = {
            "_computed_keys": [],
            "traefik-config-map": {
                "traefik": {"tls-ca": "dGVzdC1jYQ=="},
            },
            "keystone-channel": OPENSTACK_CHANNEL,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        # Use test_manifest which does NOT contain traefik config-map
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            # traefik-config-map should be removed since it's no longer in manifest
            assert "traefik-config-map" not in applied_tfvars

    def test_source_tracking_stale_manifest_values_removed(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test that manifest values are removed when no longer in manifest."""
        tfplan = "openstack-plan"
        # DB has old manifest value (not computed)
        read_config.return_value = {
            "_computed_keys": [],  # Not computed
            "neutron-revision": 123,  # Was in manifest, now removed
            "keystone-channel": OPENSTACK_CHANNEL,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            # neutron-revision should be removed (not in manifest anymore)
            assert "neutron-revision" not in applied_tfvars

    def test_source_tracking_computed_persists_without_override(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test computed values persist even when override_tfvars not provided."""
        tfplan = "openstack-plan"
        # DB has computed value
        read_config.return_value = {
            "_computed_keys": ["vault-config", "ha-scale"],
            "vault-config": {"common_name": "example.com"},
            "ha-scale": 3,
            "keystone-channel": OPENSTACK_CHANNEL,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            # No override_tfvars provided
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            # Computed values should persist
            assert applied_tfvars.get("vault-config") == {"common_name": "example.com"}
            assert applied_tfvars.get("ha-scale") == 3

    def test_merge_charm_configs_preserves_all_fields(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test charm config merging preserves all source fields."""
        tfplan = "openstack-plan"
        # DB has computed config with some fields
        read_config.return_value = {
            "_computed_keys": ["glance-config"],
            "glance-config": {"computed_field": "from_db", "shared_field": "db_value"},
        }
        # Override provides additional fields and conflicts
        extra_tfvars = {
            "glance-config": {
                "override_field": "from_override",
                "shared_field": "override_value",
            },
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", extra_tfvars
            )
            applied_tfvars = write_tfvars.call_args.args[0]

            # All fields should be present
            assert "computed_field" in applied_tfvars["glance-config"]
            assert "override_field" in applied_tfvars["glance-config"]
            assert "ceph-osd-replication-count" in applied_tfvars["glance-config"]

            # Precedence: manifest > override > computed for conflicts
            # Manifest has ceph-osd-replication-count: 5
            assert applied_tfvars["glance-config"]["ceph-osd-replication-count"] == 5
            # Fields only in DB should be preserved
            assert applied_tfvars["glance-config"]["computed_field"] == "from_db"
            # Fields only in override should be included
            assert applied_tfvars["glance-config"]["override_field"] == "from_override"
            # For shared_field: override provides it, then manifest merges in
            # Since manifest doesn't have shared_field, override value wins
            assert applied_tfvars["glance-config"]["shared_field"] == "override_value"

    def test_partial_update_only_specified_charms(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test partial update only refreshes specified charms."""
        tfplan = "openstack-plan"
        # DB has values for multiple charms
        read_config.return_value = {
            "_computed_keys": ["vault-config"],
            "keystone-channel": "2023.2/stable",
            "keystone-revision": 100,
            "glance-channel": "2023.2/stable",
            "glance-revision": 50,
            "vault-config": {"common_name": "example.com"},
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            # Only update keystone charm
            tfhelper.update_partial_tfvars_and_apply_tf(
                client, manifest, ["keystone-k8s"], "fake-config"
            )
            applied_tfvars = write_tfvars.call_args.args[0]

            # Keystone values should be refreshed from manifest
            assert applied_tfvars.get("keystone-channel") == "2023.1/stable"
            assert applied_tfvars.get("keystone-revision") == 234

            # Glance values should be preserved (not updated)
            assert applied_tfvars.get("glance-channel") == "2023.2/stable"
            assert applied_tfvars.get("glance-revision") == 50

            # Computed values should be preserved
            assert applied_tfvars.get("vault-config") == {"common_name": "example.com"}

    def test_partial_update_preserves_computed_values(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test partial update preserves all computed values."""
        tfplan = "openstack-plan"
        # DB has computed values and charm values
        read_config.return_value = {
            "_computed_keys": ["vault-config", "traefik-config"],
            "glance-channel": "old/stable",
            "vault-config": {"common_name": "example.com"},
            "traefik-config": {"external_hostname": "internal.example.com"},
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            # Update glance charm only
            tfhelper.update_partial_tfvars_and_apply_tf(
                client, manifest, ["glance-k8s"], "fake-config"
            )
            applied_tfvars = write_tfvars.call_args.args[0]

            # Glance should be updated
            assert applied_tfvars.get("glance-channel") == "2023.1/stable"

            # All computed values should be preserved
            assert applied_tfvars.get("vault-config") == {"common_name": "example.com"}
            assert applied_tfvars.get("traefik-config") == {
                "external_hostname": "internal.example.com"
            }

    def test_partial_update_with_charm_config_merging(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test partial update merges charm configs correctly."""
        tfplan = "openstack-plan"
        # DB has charm config with custom fields
        read_config.return_value = {
            "_computed_keys": ["glance-config"],
            "glance-config": {
                "ceph-osd-replication-count": 3,
                "custom-field": "preserved",
            },
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            # Update glance charm
            tfhelper.update_partial_tfvars_and_apply_tf(
                client, manifest, ["glance-k8s"], "fake-config"
            )
            applied_tfvars = write_tfvars.call_args.args[0]

            # Manifest value should win for conflicts
            assert applied_tfvars["glance-config"]["ceph-osd-replication-count"] == 5
            # Custom fields should be preserved
            assert applied_tfvars["glance-config"]["custom-field"] == "preserved"

    def test_clear_computed_values_with_none_vs_empty(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test that None properly clears computed values in override_tfvars.

        This is critical for scenarios like disabling TLS vault where
        vault-config has a computed common_name field that needs to be removed.
        Both {} and None do replacement for non-charm-config keys, but None
        is more explicit and handles all cases correctly.
        """
        tfplan = "openstack-plan"
        # DB has computed vault-config with common_name (added by TLS feature)
        read_config.return_value = {
            "_computed_keys": ["vault-config"],
            "vault-config": {"common_name": "example.com"},
            "keystone-channel": OPENSTACK_CHANNEL,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)

        # Test 1: Using {} (empty dict) - replaces with empty dict
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", {"vault-config": {}}
            )
            applied_tfvars = write_tfvars.call_args.args[0]
            # vault-config is replaced with empty dict (common_name removed)
            assert applied_tfvars.get("vault-config") == {}

        # Test 2: Using None - replaces with None, more explicit clearing
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", {"vault-config": None}
            )
            applied_tfvars = write_tfvars.call_args.args[0]
            # vault-config is None (common_name removed, more explicit)
            assert applied_tfvars.get("vault-config") is None

    def test_manifest_rabbitmq_storage_maps_to_tfvar(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test rabbitmq storage is passed through from manifest to tfvars."""
        tfplan = "openstack-plan"
        read_config.return_value = {}

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {
            "data": test_manifest_with_rabbitmq_storage
        }
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            assert applied_tfvars["rabbitmq-storage"] == {"rabbitmq-data": "4G"}

    def test_manifest_rabbitmq_storage_not_preserved_without_manifest(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Test stale rabbitmq storage is removed when missing from manifest."""
        tfplan = "openstack-plan"
        read_config.return_value = {
            "_computed_keys": [],
            "rabbitmq-storage": {"rabbitmq-data": "4G"},
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(client, manifest, "fake-config", None)
            applied_tfvars = write_tfvars.call_args.args[0]

            assert "rabbitmq-storage" not in applied_tfvars


class TestApplyTfvars:
    """Unit tests for TerraformHelper._apply_tfvars charm-config merging behaviour."""

    # Minimal tfvar_map that recognises octavia-config as a charm config key
    _OCTAVIA_TFVAR_MAP = {
        "charms": {
            "octavia-k8s": {
                "channel": "octavia-channel",
                "revision": "octavia-revision",
                "config": "octavia-config",
            }
        }
    }

    def _make_helper(self, mocker, snap, tfvar_map=None):
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        return TerraformHelper(
            path=Path("/tmp/test"),
            plan="openstack-plan",
            tfvar_map=tfvar_map or self._OCTAVIA_TFVAR_MAP,
        )

    def test_empty_dict_replaces_existing_charm_config(self, mocker, snap):
        """Passing {} as override for a charm-config key must replace it, not merge.

        Regression test: previously _apply_tfvars called target_value.update({})
        which was a no-op, leaving the old Amphora octavia-config untouched after
        'sunbeam disable loadbalancer'.
        """
        helper = self._make_helper(mocker, snap)
        target = {
            "octavia-config": {
                "amphora-network-attachment": "openstack/octavia-mgmt-net",
                "amp-image-tag": "octavia-amphora",
                "amp-flavor-id": "daf37b10",
            }
        }
        source = {"octavia-config": {}}
        helper._apply_tfvars(target, source)
        assert target["octavia-config"] == {}

    def test_non_empty_dict_merges_into_existing_charm_config(self, mocker, snap):
        """Non-empty charm-config override still merges (existing keys preserved)."""
        helper = self._make_helper(mocker, snap)
        target = {
            "octavia-config": {
                "amp-image-tag": "old-tag",
                "existing-key": "keep-me",
            }
        }
        source = {"octavia-config": {"amp-image-tag": "new-tag"}}
        helper._apply_tfvars(target, source)
        assert target["octavia-config"]["amp-image-tag"] == "new-tag"
        assert target["octavia-config"]["existing-key"] == "keep-me"

    def test_none_replaces_existing_charm_config(self, mocker, snap):
        """None as override value must unconditionally replace the existing config."""
        helper = self._make_helper(mocker, snap)
        target = {"octavia-config": {"amp-image-tag": "octavia-amphora"}}
        source = {"octavia-config": None}
        helper._apply_tfvars(target, source)
        assert target["octavia-config"] is None

    def test_non_charm_config_replaced_not_merged(self, mocker, snap):
        """Non-charm-config (non-dict) values are always replaced."""
        helper = self._make_helper(mocker, snap)
        target = {"octavia-channel": "2023.1/stable", "enable-octavia": True}
        source = {"octavia-channel": "2024.1/stable", "enable-octavia": False}
        helper._apply_tfvars(target, source)
        assert target["octavia-channel"] == "2024.1/stable"
        assert target["enable-octavia"] is False

    def test_empty_dict_clears_charm_config_in_full_disable_flow(
        self,
        mocker,
        snap,
        copytree,
        deployment: Deployment,
        read_config,
    ):
        """Integration: set_tfvars_on_disable octavia-config:{} clears stored config.

        Simulates the disable-loadbalancer flow where octavia-config was previously
        set by the Amphora configure step and must be fully cleared on disable.
        """
        tfplan = "openstack-plan"
        # DB has the full Amphora octavia-config written by configure step
        read_config.return_value = {
            "_computed_keys": ["octavia-config", "octavia-to-tls-provider"],
            "octavia-config": {
                "amphora-network-attachment": "openstack/octavia-mgmt-net",
                "amp-image-tag": "octavia-amphora",
                "amp-flavor-id": "daf37b10-6998-416a-b412-29869c7f38fa",
                "amp-secgroup-list": "55eaa2b8 8e187499",
                "amp-boot-network-list": "e6772bb5-3de6-4df2-9c4c-edeb2780eb85",
            },
            "octavia-to-tls-provider": "manual-tls-certificates",
            "enable-octavia": True,
        }

        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()

        tfhelper = deployment.get_tfhelper(tfplan)
        disable_tfvars = {
            "enable-octavia": False,
            "octavia-config": {},
            "octavia-to-tls-provider": None,
        }
        with (
            patch.object(tfhelper, "write_tfvars") as write_tfvars,
            patch.object(tfhelper, "apply"),
        ):
            tfhelper.update_tfvars_and_apply_tf(
                client, manifest, "fake-config", disable_tfvars
            )
            applied_tfvars = write_tfvars.call_args.args[0]

        assert applied_tfvars["octavia-config"] == {}
        assert applied_tfvars["octavia-to-tls-provider"] is None
        assert applied_tfvars["enable-octavia"] is False


class TestParseTerraformEvent:
    """Tests for TerraformHelper._parse_terraform_event()."""

    def _make_helper(self, mocker, snap):
        """Create a minimal TerraformHelper for testing."""
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        return TerraformHelper(
            path=Path("/tmp/test"),
            plan="test-plan",
            tfvar_map={},
        )

    def test_apply_start_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "info",
                "@message": "juju_application.keystone: Creating...",
                "@timestamp": "2026-03-23T10:00:01.000Z",
                "type": "apply_start",
                "hook": {
                    "resource": {
                        "addr": "juju_application.keystone",
                        "resource_type": "juju_application",
                        "resource_name": "keystone",
                    },
                    "action": "create",
                },
            }
        )
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.source == "terraform"
        assert event.event_type == "apply_start"
        assert "keystone" in event.message
        assert "creat" in event.message.lower()

    def test_apply_complete_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "info",
                "@message": "juju_application.keystone: Creation complete after 4s",
                "@timestamp": "2026-03-23T10:00:05.000Z",
                "type": "apply_complete",
                "hook": {
                    "resource": {
                        "addr": "juju_application.keystone",
                        "resource_type": "juju_application",
                        "resource_name": "keystone",
                    },
                    "action": "create",
                    "elapsed_seconds": 4,
                },
            }
        )
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "apply_complete"
        assert "keystone" in event.message
        assert "4" in event.message

    def test_apply_errored_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "error",
                "@message": "juju_application.keystone: error",
                "@timestamp": "2026-03-23T10:00:05.000Z",
                "type": "apply_errored",
                "hook": {
                    "resource": {"addr": "juju_application.keystone"},
                    "action": "create",
                },
            }
        )
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "apply_errored"

    def test_change_summary_event(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "info",
                "@message": "Apply complete! Resources: 3 added, 1 changed, 0 destroyed.",
                "@timestamp": "2026-03-23T10:00:06.000Z",
                "type": "change_summary",
                "changes": {"add": 3, "change": 1, "import": 0, "remove": 0},
            }
        )
        event = helper._parse_terraform_event(line)
        assert event is not None
        assert event.event_type == "change_summary"
        assert "3 added" in event.message

    def test_diagnostic_state_lock_sets_flag(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "error",
                "@message": "Error acquiring the state lock",
                "@timestamp": "2026-03-23T10:00:00.000Z",
                "type": "diagnostic",
                "diagnostic": {
                    "severity": "error",
                    "summary": "Error acquiring the state lock",
                    "detail": "state blob is already locked",
                },
            }
        )
        state_lock_detected = [False]
        event = helper._parse_terraform_event(line, state_lock_flag=state_lock_detected)
        assert state_lock_detected[0] is True
        assert event is None

    def test_unrecognized_type_returns_none(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        line = json.dumps(
            {
                "@level": "info",
                "@message": "Planning...",
                "@timestamp": "2026-03-23T10:00:00.000Z",
                "type": "planned_change",
            }
        )
        event = helper._parse_terraform_event(line)
        assert event is None

    def test_invalid_json_returns_none(self, mocker, snap):
        helper = self._make_helper(mocker, snap)
        event = helper._parse_terraform_event("not valid json {{{")
        assert event is None


class TestRunTerraformCommand:
    """Tests for TerraformHelper._run_terraform_command()."""

    def _make_helper(self, mocker, snap, tmp_path):
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        return TerraformHelper(
            path=tmp_path,
            plan="test-plan",
            tfvar_map={},
        )

    def test_successful_command_reports_events(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        json_lines = [
            json.dumps(
                {
                    "type": "apply_start",
                    "@message": "creating",
                    "@timestamp": "2026-03-23T10:00:01.000Z",
                    "hook": {"resource": {"addr": "res.a"}, "action": "create"},
                }
            )
            + "\n",
            json.dumps(
                {
                    "type": "apply_complete",
                    "@message": "created",
                    "@timestamp": "2026-03-23T10:00:05.000Z",
                    "hook": {
                        "resource": {"addr": "res.a"},
                        "action": "create",
                        "elapsed_seconds": 4,
                    },
                }
            )
            + "\n",
        ]

        mock_process = MagicMock()
        mock_process.stdout = iter(json_lines)
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        reporter = Mock()

        with patch("subprocess.Popen", return_value=mock_process):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=reporter,
            )

        assert reporter.report.call_count == 2
        events = [call.args[0] for call in reporter.report.call_args_list]
        assert events[0].event_type == "apply_start"
        assert events[1].event_type == "apply_complete"

    def test_failed_command_raises_terraform_exception(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr.read.return_value = "Error: something failed"
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_state_lock_from_diagnostic_raises_state_lock_exception(
        self, mocker, snap, tmp_path
    ):
        helper = self._make_helper(mocker, snap, tmp_path)
        lock_line = (
            json.dumps(
                {
                    "type": "diagnostic",
                    "@level": "error",
                    "@message": "Error acquiring the state lock",
                    "@timestamp": "2026-03-23T10:00:00.000Z",
                    "diagnostic": {
                        "severity": "error",
                        "summary": "Error acquiring the state lock",
                        "detail": "state blob is already locked",
                    },
                }
            )
            + "\n"
        )

        mock_process = MagicMock()
        mock_process.stdout = iter([lock_line])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformStateLockedException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_state_lock_from_stderr_fallback(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr.read.return_value = "Error: remote state already locked"
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with (
            patch("subprocess.Popen", return_value=mock_process),
            pytest.raises(TerraformStateLockedException),
        ):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=NoOpReporter(),
            )

    def test_none_reporter_still_works(self, mocker, snap, tmp_path):
        helper = self._make_helper(mocker, snap, tmp_path)
        json_line = (
            json.dumps(
                {
                    "type": "apply_start",
                    "@message": "creating",
                    "@timestamp": "2026-03-23T10:00:01.000Z",
                    "hook": {"resource": {"addr": "res.a"}, "action": "create"},
                }
            )
            + "\n"
        )

        mock_process = MagicMock()
        mock_process.stdout = iter([json_line])
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            helper._run_terraform_command(
                cmd=["terraform", "apply", "-json"],
                env={},
                reporter=None,
            )
