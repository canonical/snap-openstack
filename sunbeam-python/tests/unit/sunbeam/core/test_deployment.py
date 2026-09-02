# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import functools
from pathlib import Path
from unittest.mock import Mock, call, patch

import pydantic
import pytest
import yaml

import sunbeam.core.deployment as deployment_mod
import sunbeam.core.manifest as manifest_mod
import sunbeam.core.terraform as terraform_mod
from sunbeam.core.deployment import Deployment
from sunbeam.versions import (
    MANIFEST_CHARM_VERSIONS,
    OPENSTACK_CHANNEL,
    TERRAFORM_DIR_NAMES,
)

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
    terraform:
      openstack-plan:
        source: /home/ubuntu/openstack-tf
      hypervisor-plan:
        source: /home/ubuntu/hypervisor-tf
"""


@pytest.fixture()
def deployment(mocker, snap):
    mocker.patch.object(manifest_mod, "Snap", return_value=snap)
    mocker.patch.object(deployment_mod, "Snap", return_value=snap)
    snap_config = {"deployment.risk": "stable"}
    snap.config.get.side_effect = snap_config.__getitem__
    with patch("sunbeam.core.deployment.Deployment") as p:
        dep = p(name="", url="", type="")
        dep.get_manifest.side_effect = functools.partial(Deployment.get_manifest, dep)
        dep.get_tfhelper.side_effect = functools.partial(Deployment.get_tfhelper, dep)
        dep.parse_manifest.side_effect = functools.partial(
            Deployment.parse_manifest, dep
        )
        dep.parse_feature_manifest.side_effect = functools.partial(
            Deployment.parse_feature_manifest, dep
        )
        dep._load_tfhelpers.side_effect = functools.partial(
            Deployment._load_tfhelpers, dep
        )
        dep.get_client.side_effect = ValueError("No clusterd in testing...")
        dep.plans_directory = Path("/tmp/plans")
        dep.__setattr__("_tfhelpers", {})
        dep._manifest = None
        dep.__setattr__("name", "test_deployment")
        dep.get_feature_manager.return_value = Mock(
            get_all_feature_manifests=Mock(return_value={}),
            get_all_feature_manifest_tfvar_map=Mock(return_value={}),
        )
        yield dep


class TestDeployment:
    def test_get_default_manifest(self, deployment: Deployment):
        manifest = deployment.get_manifest()

        # Assert core charms / plans are present
        assert (
            set(manifest.core.software.charms.keys()) >= MANIFEST_CHARM_VERSIONS.keys()
        )
        assert (
            set(manifest.core.software.terraform.keys()) >= TERRAFORM_DIR_NAMES.keys()
        )

    def test_parse_manifest_warns_for_unknown_top_level_keys(
        self, deployment: Deployment, caplog
    ):
        with caplog.at_level("WARNING", logger="sunbeam.core.manifest"):
            manifest = deployment.parse_manifest(
                {
                    "preseed": {
                        "user": {"remote_access_location": "remote"},
                    },
                }
            )
        assert "Unknown manifest key: preseed" in caplog.text
        assert "preseed" not in manifest.model_dump()

    def test_parse_manifest_warns_for_unknown_nested_keys(
        self, deployment: Deployment, caplog
    ):
        with caplog.at_level("WARNING", logger="sunbeam.core.manifest"):
            manifest = deployment.parse_manifest(
                {
                    "core": {
                        "config": {
                            "user": {
                                "remote_access_locaion": "remote",
                            },
                        },
                    },
                }
            )
        assert "Unknown manifest key: remote_access_locaion" in caplog.text
        assert "remote_access_locaion" not in manifest.core.config.user.model_dump()

    def test_parse_manifest_warns_for_alias_only_field_name(
        self, deployment: Deployment, caplog
    ):
        with caplog.at_level("WARNING", logger="sunbeam.core.manifest"):
            manifest = deployment.parse_manifest(
                {"core": {"config": {"k8s_addons": {"loadbalancer": "metallb"}}}}
            )
        assert "Unknown manifest key: k8s_addons" in caplog.text
        assert manifest.core.config.k8s_addons is None

    def test_parse_manifest_preserves_invalid_value_errors(
        self, deployment: Deployment
    ):
        with pytest.raises(pydantic.ValidationError) as exc_info:
            deployment.parse_manifest(
                {
                    "preseed": {},
                    "core": {
                        "config": {
                            "external-network": {"network_type": "invalid"},
                        },
                    },
                }
            )
        assert exc_info.value.errors()[0]["loc"] == (
            "core",
            "config",
            "external-network",
            "network_type",
        )

    def test_parse_manifest_warns_for_unknown_features(
        self, deployment: Deployment, caplog
    ):
        deployment.get_feature_manager.return_value.features.return_value = {}
        deployment.get_feature_manager.return_value.groups.return_value = {}

        with caplog.at_level("WARNING", logger="sunbeam.core.deployment"):
            manifest = deployment.parse_manifest({"features": {"loadbalncer": {}}})
        assert "Feature loadbalncer is not found in feature manager" in caplog.text
        assert manifest.features == {}

    def test_parse_manifest_does_not_mutate_input(self, deployment: Deployment):
        manifest_data = {
            "core": {"config": {"user": {"remote_access_location": "remote"}}},
            "features": {},
            "storage": {},
        }
        original_manifest_data = copy.deepcopy(manifest_data)

        deployment.parse_manifest(manifest_data)

        assert manifest_data == original_manifest_data

    def test_load_on_default(self, deployment: Deployment, tmpdir):
        manifest_file = tmpdir.mkdir("manifests").join("test_manifest.yaml")
        manifest_file.write(test_manifest)
        manifest_obj = deployment.get_manifest(manifest_file)

        # Check updates from manifest file
        ks_manifest = manifest_obj.core.software.charms["keystone-k8s"]
        assert ks_manifest.channel == "2023.1/stable"
        assert ks_manifest.revision == 234
        assert ks_manifest.config == {"debug": True}

        # Check default ones
        nova_manifest = manifest_obj.core.software.charms["nova-k8s"]
        assert nova_manifest.channel == OPENSTACK_CHANNEL
        assert nova_manifest.revision is None
        assert nova_manifest.config is None

    def test_load_latest_from_clusterdb(self, deployment: Deployment):
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        deployment.get_client.side_effect = None
        deployment.get_client.return_value = client
        manifest = deployment.get_manifest()
        ks_manifest = manifest.core.software.charms["keystone-k8s"]
        assert ks_manifest.channel == "2023.1/stable"
        assert ks_manifest.revision == 234
        assert ks_manifest.config == {"debug": True}

        # Assert defaults unchanged
        nova_manifest = manifest.core.software.charms["nova-k8s"]
        assert nova_manifest.channel == OPENSTACK_CHANNEL
        assert nova_manifest.revision is None
        assert nova_manifest.config is None

    def test_invalid_manifest_value_from_clusterdb_is_not_ignored(
        self, deployment: Deployment
    ):
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {
            "data": "core:\n  config:\n    external-network:\n"
            "      network_type: invalid\n"
        }
        deployment.get_client.side_effect = None
        deployment.get_client.return_value = client

        with pytest.raises(pydantic.ValidationError, match="literal_error"):
            deployment.get_manifest()

    def test_get_tfhelper(self, mocker, snap, copytree, deployment: Deployment):
        tfplan = "k8s-plan"
        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        tfhelper = deployment.get_tfhelper(tfplan)
        assert tfhelper.plan == tfplan
        assert deployment._load_tfhelpers.call_count == 1
        copytree.assert_has_calls(
            [
                call(
                    Path(snap.paths.snap / "etc" / tfplan_dir),
                    Path(deployment.plans_directory / tfplan_dir),
                    dirs_exist_ok=True,
                )
                for tfplan_dir in TERRAFORM_DIR_NAMES.values()
            ],
            any_order=True,
        )

    def test_get_tfhelper_tfplan_override_in_manifest(
        self, mocker, snap, copytree, deployment: Deployment
    ):
        tfplan = "openstack-plan"
        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        client = Mock()
        client.cluster.get_latest_manifest.return_value = {"data": test_manifest}
        client.cluster.get_config.return_value = "{}"
        deployment.get_client.side_effect = None
        deployment.get_client.return_value = client
        tfhelper = deployment.get_tfhelper(tfplan)
        tfplan_dir = TERRAFORM_DIR_NAMES.get(tfplan)
        test_manifest_dict = yaml.safe_load(test_manifest)
        copytree.assert_any_call(
            Path(
                test_manifest_dict["core"]["software"]["terraform"]["openstack-plan"][
                    "source"
                ]
            ),
            Path(deployment.plans_directory / tfplan_dir),
            dirs_exist_ok=True,
        )
        assert tfhelper.plan == tfplan

    def test_get_tfhelper_multiple_calls(
        self, mocker, snap, copytree, deployment: Deployment
    ):
        tfplan = "k8s-plan"
        mocker.patch.object(deployment_mod, "Snap", return_value=snap)
        mocker.patch.object(manifest_mod, "Snap", return_value=snap)
        mocker.patch.object(terraform_mod, "Snap", return_value=snap)
        tfhelper = deployment.get_tfhelper(tfplan)
        assert tfhelper.plan == tfplan
        assert deployment._load_tfhelpers.call_count == 1
        # _load_tfhelpers should be cached
        tfhelper = deployment.get_tfhelper(tfplan)
        assert deployment._load_tfhelpers.call_count == 1
