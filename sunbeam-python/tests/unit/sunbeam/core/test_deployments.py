# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import errno
import os

import pytest
import yaml

from sunbeam.core.deployments import DeploymentsConfig

ORIGINAL = "active: mycloud\ndeployments: []\n"


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "deployments.yaml"
    path.write_text(ORIGINAL)
    return path


def test_write_preserves_existing_config_when_replace_fails(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    mocker.patch(
        "sunbeam.core.deployments.os.replace",
        side_effect=OSError("replace failed"),
    )

    with pytest.raises(OSError, match="replace failed"):
        config.write()

    assert config_path.read_text() == ORIGINAL
    assert list(config_path.parent.iterdir()) == [config_path]


def test_write_preserves_existing_config_when_disk_is_full(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    config.active = "other"
    mocker.patch(
        "sunbeam.core.deployments.os.fsync",
        side_effect=OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)),
    )

    with pytest.raises(OSError) as excinfo:
        config.write()

    assert excinfo.value.errno == errno.ENOSPC
    assert config_path.read_text() == ORIGINAL
    assert list(config_path.parent.iterdir()) == [config_path]


def test_write_does_not_modify_existing_file_in_place(config_path):
    config = DeploymentsConfig.load(config_path)
    config.active = "other"

    with config_path.open() as reader:
        config.write()

        assert reader.read() == ORIGINAL

    assert DeploymentsConfig.load(config_path).active == "other"


def test_write_replaces_config_on_success(config_path):
    config = DeploymentsConfig.load(config_path)
    config.active = "new"

    config.write()

    assert yaml.safe_load(config_path.read_text()) == {
        "active": "new",
        "deployments": [],
    }
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert list(config_path.parent.iterdir()) == [config_path]


def test_write_updates_symlink_target(config_path, tmp_path_factory):
    link = tmp_path_factory.mktemp("link") / "deployments.yaml"
    link.symlink_to(config_path)
    config = DeploymentsConfig.load(link)
    config.active = "new"

    config.write()

    assert link.is_symlink()
    assert DeploymentsConfig.load(config_path).active == "new"
    assert list(link.parent.iterdir()) == [link]
    assert list(config_path.parent.iterdir()) == [config_path]
