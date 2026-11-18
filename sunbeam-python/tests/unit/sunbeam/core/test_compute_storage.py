# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for encrypted compute-storage configuration."""

from unittest import mock

import pydantic
import pytest

from sunbeam.core import compute_storage


class TestComputeStorageConfig:
    def test_load_valid_config(self, tmp_path):
        config = tmp_path / "compute-storage.yaml"
        config.write_text(
            "vault_offer_url: vault-controller:admin/vault.vault-kv\n"
            "nodes:\n"
            "  compute-1.example.com:\n"
            "    target: /dev/disk/by-id/wwn-0x1\n"
            "    existing_key_secret_id: secret:abc\n"
        )

        loaded = compute_storage.load_compute_storage_config(config)

        assert loaded.vault_offer_url == "vault-controller:admin/vault.vault-kv"
        assert loaded.nodes["compute-1.example.com"].target == (
            "/dev/disk/by-id/wwn-0x1"
        )
        assert loaded.nodes["compute-1.example.com"].existing_key_secret_id == (
            "secret:abc"
        )

    def test_load_config_without_nodes(self, tmp_path):
        config = tmp_path / "compute-storage.yaml"
        config.write_text("vault_offer_url: vault.vault-kv\n")

        loaded = compute_storage.load_compute_storage_config(config)

        assert loaded.nodes == {}

    def test_load_rejects_missing_vault_offer(self, tmp_path):
        config = tmp_path / "compute-storage.yaml"
        config.write_text("nodes: {}\n")

        with pytest.raises(pydantic.ValidationError):
            compute_storage.load_compute_storage_config(config)

    def test_load_rejects_incomplete_node(self, tmp_path):
        config = tmp_path / "compute-storage.yaml"
        config.write_text(
            "vault_offer_url: vault.vault-kv\n"
            "nodes:\n"
            "  compute-1.example.com:\n"
            "    target: /dev/disk/by-id/wwn-0x1\n"
        )

        with pytest.raises(pydantic.ValidationError):
            compute_storage.load_compute_storage_config(config)


class TestVaultOfferConfig:
    def test_get_returns_none_when_not_configured(self):
        from sunbeam.clusterd.service import ConfigItemNotFoundException

        client = mock.MagicMock()
        with mock.patch.object(
            compute_storage,
            "read_config",
            side_effect=ConfigItemNotFoundException,
        ):
            assert compute_storage.get_vault_kv_offer_url(client) is None

    def test_get_returns_configured_value(self):
        client = mock.MagicMock()
        with mock.patch.object(
            compute_storage,
            "read_config",
            return_value={"vault_kv_offer_url": "vault.vault-kv"},
        ):
            assert compute_storage.get_vault_kv_offer_url(client) == "vault.vault-kv"

    def test_set_stores_value(self):
        client = mock.MagicMock()
        with (
            mock.patch.object(compute_storage, "read_config", return_value={}),
            mock.patch.object(compute_storage, "update_config") as update,
        ):
            compute_storage.set_vault_kv_offer_url(client, "vault.vault-kv")

        update.assert_called_once_with(
            client,
            compute_storage.VAULT_KV_OFFER_CONFIG_KEY,
            {"vault_kv_offer_url": "vault.vault-kv"},
        )

    def test_set_same_value_is_idempotent(self):
        client = mock.MagicMock()
        with (
            mock.patch.object(
                compute_storage,
                "read_config",
                return_value={"vault_kv_offer_url": "vault.vault-kv"},
            ),
            mock.patch.object(compute_storage, "update_config") as update,
        ):
            compute_storage.set_vault_kv_offer_url(client, "vault.vault-kv")

        update.assert_called_once()

    def test_set_different_value_rejected(self):
        client = mock.MagicMock()
        with (
            mock.patch.object(
                compute_storage,
                "read_config",
                return_value={"vault_kv_offer_url": "old.vault-kv"},
            ),
            mock.patch.object(compute_storage, "update_config") as update,
        ):
            with pytest.raises(ValueError, match="different vault-kv offer"):
                compute_storage.set_vault_kv_offer_url(client, "new.vault-kv")

        update.assert_not_called()
