# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.k8s import K8SNodeNotFoundError, find_node


class _Api404Error(Exception):
    status = Mock(code=404)


class TestFindNode:
    """Tests for find_node fallback matching (LP#2129793)."""

    def test_find_node_falls_back_to_short_hostname(self, monkeypatch):
        """A node is found by short name when the k8s name is the FQDN."""
        fake_exceptions = Mock()
        fake_exceptions.ApiError = _Api404Error
        monkeypatch.setattr("sunbeam.core.k8s.l_exceptions", fake_exceptions)

        client = Mock()
        client.get.side_effect = _Api404Error()
        node = Mock()
        node.metadata.name = "node1.example.com"
        client.list.return_value = iter([node])

        assert find_node(client, "node1") is node

    def test_find_node_not_found(self, monkeypatch):
        """K8SNodeNotFoundError is raised when nothing matches."""
        fake_exceptions = Mock()
        fake_exceptions.ApiError = _Api404Error
        monkeypatch.setattr("sunbeam.core.k8s.l_exceptions", fake_exceptions)

        client = Mock()
        client.get.side_effect = _Api404Error()
        client.list.return_value = iter([])

        with pytest.raises(K8SNodeNotFoundError):
            find_node(client, "nope")

    def test_find_node_ambiguous_short_hostname(self, monkeypatch):
        """Ambiguous short hostname matches raise instead of picking one."""
        fake_exceptions = Mock()
        fake_exceptions.ApiError = _Api404Error
        monkeypatch.setattr("sunbeam.core.k8s.l_exceptions", fake_exceptions)

        client = Mock()
        client.get.side_effect = _Api404Error()
        node_a, node_b = Mock(), Mock()
        node_a.metadata.name = "node1.a.example.com"
        node_b.metadata.name = "node1.b.example.com"
        client.list.return_value = iter([node_a, node_b])

        with pytest.raises(K8SNodeNotFoundError):
            find_node(client, "node1")
