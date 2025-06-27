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
        assert applied_tfvars.get("glance-config") == {"ceph-osd-replication-count": 5}

        # While mysql-config is not in the manifest, it is in config db,
        # and mysql-config is one of the preserve vars of the OpenStack plan
        assert applied_tfvars.get("mysql-config") == {"debug": True}
