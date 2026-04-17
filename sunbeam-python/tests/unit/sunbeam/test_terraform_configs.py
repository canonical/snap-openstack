# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

REPO_ROOT = Path(__file__).parents[4]


def test_hypervisor_ceph_relation_has_moved_block():
    """The brownfield Ceph relation should move to the keyed generic resource."""
    tf_file = REPO_ROOT / "cloud/etc/deploy-openstack-hypervisor/main.tf"
    tf_config = tf_file.read_text()

    assert "from = juju_integration.hypervisor-cinder-ceph[0]" in tf_config
    assert (
        "to   = juju_integration.hypervisor-extra-integration"
        '["cinder-volume-ceph-ceph-access"]'
    ) in tf_config


def test_storage_backend_secret_resources_have_moved_blocks():
    """Existing backend secrets should move to their counted addresses."""
    tf_file = REPO_ROOT / "cloud/etc/deploy-storage/modules/backend/main.tf"
    tf_config = tf_file.read_text()

    assert "from = juju_secret.secret" in tf_config
    assert "to   = juju_secret.secret[0]" in tf_config
    assert "from = juju_access_secret.secret-access" in tf_config
    assert "to   = juju_access_secret.secret-access[0]" in tf_config
