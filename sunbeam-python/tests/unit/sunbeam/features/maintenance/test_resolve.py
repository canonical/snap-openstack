# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import click
import pytest

from sunbeam.features.maintenance.resolve import ResolvedNode


class TestResolvedNode:
    """Tests for ResolvedNode name resolution (LP#2129793)."""

    def _deployment(self):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack-machines"
        return deployment

    def test_hostname_exact_and_fqdn(self):
        """The CLI argument matches cluster list keys exactly or by short name."""
        cluster_status = {"node1": "compute"}
        deployment = self._deployment()

        assert ResolvedNode("node1", deployment, cluster_status, Mock()).hostname == (
            "node1"
        )
        assert ResolvedNode(
            "node1.example.com", deployment, cluster_status, Mock()
        ).hostname == ("node1")

    def test_hostname_not_found(self):
        """An unknown node raises the 'does not exist in cluster' error."""
        deployment = self._deployment()
        with pytest.raises(click.ClickException):
            ResolvedNode("nope", deployment, {"node1": "compute"}, Mock()).hostname

    def test_clusterd_name_via_machine_id(self):
        """The clusterd node name is resolved from the juju machine id.

        Local deployments register nodes in clusterd with the FQDN while juju
        reports the short hostname.
        """
        deployment = self._deployment()
        jhelper = Mock()
        machine = Mock(hostname="node1", dns_name="10.0.0.1")
        jhelper.get_machines.return_value = {"0": machine}
        deployment.get_client.return_value.cluster.list_nodes.return_value = [
            {"name": "node1.example.com", "machineid": 0}
        ]

        resolved = ResolvedNode("node1", deployment, {"node1": "compute"}, jhelper)
        assert resolved.clusterd_name == "node1.example.com"

    def test_hypervisor_name_short_and_fqdn(self):
        """The nova hypervisor hostname is matched by short name."""
        deployment = self._deployment()
        jhelper = Mock()
        with pytest.MonkeyPatch.context() as mp:
            conn = Mock()
            hyp = Mock()
            hyp.name = "node1.example.com"
            conn.compute.hypervisors.return_value = [hyp]
            mp.setattr(
                "sunbeam.features.maintenance.resolve.get_admin_connection",
                lambda jh, d: conn,
            )
            resolved = ResolvedNode("node1", deployment, {"node1": "compute"}, jhelper)
            assert resolved.hypervisor_name == "node1.example.com"

    def _clusterd(self, deployment, jhelper):
        """Wire mocks so clusterd_name resolves via the juju machine id."""
        machine = Mock(hostname="node1", dns_name="10.0.0.1")
        jhelper.get_machines.return_value = {"0": machine}
        deployment.get_client.return_value.cluster.list_nodes.return_value = [
            {"name": "node1.example.com", "machineid": 0}
        ]

    def test_k8s_name_short_and_fqdn(self):
        """The k8s node name is resolved via the sunbeam/hostname label."""
        deployment = self._deployment()
        jhelper = Mock()
        self._clusterd(deployment, jhelper)
        kube_client = Mock()
        node = Mock()
        node.metadata.name = "node1"
        kube_client.list.return_value = [node]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "sunbeam.features.maintenance.resolve.get_kube_client",
                lambda client: kube_client,
            )
            resolved = ResolvedNode("node1", deployment, {"node1": "compute"}, jhelper)
            assert resolved.k8s_name == "node1"

    def test_k8s_name_falls_back_to_hostname_match(self):
        """Without the sunbeam/hostname label the short-hostname match is used."""
        deployment = self._deployment()
        jhelper = Mock()
        self._clusterd(deployment, jhelper)
        kube_client = Mock()
        node = Mock()
        node.metadata.name = "node1"
        kube_client.list.side_effect = [[], [node]]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "sunbeam.features.maintenance.resolve.get_kube_client",
                lambda client: kube_client,
            )
            resolved = ResolvedNode("node1", deployment, {"node1": "compute"}, jhelper)
            assert resolved.k8s_name == "node1"
