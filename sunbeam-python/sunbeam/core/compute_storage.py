# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Compute-storage configuration for encrypted Nova instance storage.

This is deliberately a standalone configuration model rather than a
section of the bootstrap-time Sunbeam manifest, and it is loaded from a
dedicated file passed to ``sunbeam configure compute-storage --config``.
"""

import logging
from pathlib import (
    Path,
)

import pydantic
import yaml

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    read_config,
    update_config,
)

LOG = logging.getLogger(__name__)

VAULT_KV_OFFER_CONFIG_KEY = "VaultKvOfferConfig"


class NodeStorageConfig(pydantic.BaseModel):
    """Encrypted storage configuration for a single compute host."""

    target: str
    existing_key_secret_id: str


class ComputeStorageConfig(pydantic.BaseModel):
    """Standalone encrypted compute-storage configuration."""

    vault_offer_url: str
    nodes: dict[str, NodeStorageConfig] = {}


def load_compute_storage_config(path: Path) -> ComputeStorageConfig:
    """Load a compute-storage configuration file.

    :param path: path to the YAML configuration file.
    :returns: parsed compute-storage configuration.
    """
    data = yaml.safe_load(Path(path).read_text())
    return ComputeStorageConfig.model_validate(data)


def get_vault_kv_offer_url(client) -> str | None:
    """Return the cluster-wide vault-kv offer URL, if configured."""
    try:
        config = read_config(client, VAULT_KV_OFFER_CONFIG_KEY)
    except ConfigItemNotFoundException:
        return None
    return config.get("vault_kv_offer_url")


def set_vault_kv_offer_url(client, url: str) -> None:
    """Store the cluster-wide vault-kv offer URL.

    This is a convenience guard only: it rejects changing an offer URL
    that was already stored, producing an early, friendly error. The
    authoritative protection against operating against the wrong Vault
    cluster is vaultlocker's own cluster-identity pinning.

    :raises ValueError: if a different offer URL is already configured.
    """
    existing = get_vault_kv_offer_url(client)
    if existing is not None and existing != url:
        raise ValueError(
            f"A different vault-kv offer URL is already configured: {existing}"
        )
    update_config(client, VAULT_KV_OFFER_CONFIG_KEY, {"vault_kv_offer_url": url})
