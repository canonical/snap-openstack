# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import functools
from unittest.mock import Mock, patch

import pytest

import sunbeam.core.deployment as deployment_mod
import sunbeam.core.manifest as manifest_mod
import sunbeam.core.terraform as terraform_mod
from sunbeam.core.deployment import Deployment
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
