# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import errno
import os
import stat

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


def test_write_restricts_permissions_before_writing(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    safe_dump = yaml.safe_dump
    modes = []

    def record_mode(data, stream):
        modes.append(os.fstat(stream.fileno()).st_mode & 0o777)
        safe_dump(data, stream)

    mocker.patch("sunbeam.core.deployments.yaml.safe_dump", side_effect=record_mode)
    previous_umask = os.umask(0o277)
    try:
        config.write()
    finally:
        os.umask(previous_umask)

    assert modes == [0o600]
    assert config_path.stat().st_mode & 0o777 == 0o600


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


def test_write_syncs_parent_directory_after_replace(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    events = []
    replace, fsync = os.replace, os.fsync

    def record_replace(src, dst):
        events.append("replace")
        replace(src, dst)

    def record_fsync(fd):
        opened = os.fstat(fd)
        if stat.S_ISDIR(opened.st_mode):
            assert os.path.samestat(opened, config_path.parent.stat())
            events.append("fsync directory")
        else:
            events.append("fsync file")
        fsync(fd)

    mocker.patch("sunbeam.core.deployments.os.replace", side_effect=record_replace)
    mocker.patch("sunbeam.core.deployments.os.fsync", side_effect=record_fsync)

    config.write()

    assert events == ["fsync file", "replace", "fsync directory"]


@pytest.mark.parametrize(
    "failing_call, error",
    [
        # A directory can be writable without being readable
        ("open", PermissionError(errno.EACCES, os.strerror(errno.EACCES))),
        ("fsync", OSError(errno.EIO, os.strerror(errno.EIO))),
    ],
)
def test_write_keeps_new_config_when_directory_cannot_be_synced(
    config_path, mocker, caplog, failing_call, error
):
    config = DeploymentsConfig.load(config_path)
    config.active = "other"
    os_open, fsync = os.open, os.fsync

    def open_file(path, flags, *args, **kwargs):
        if failing_call == "open" and flags & os.O_DIRECTORY:
            raise error
        return os_open(path, flags, *args, **kwargs)

    def sync_file(fd):
        if failing_call == "fsync" and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise error
        fsync(fd)

    mocker.patch("sunbeam.core.deployments.os.open", side_effect=open_file)
    mocker.patch("sunbeam.core.deployments.os.fsync", side_effect=sync_file)

    with caplog.at_level("WARNING", logger="sunbeam.core.deployments"):
        config.write()

    assert DeploymentsConfig.load(config_path).active == "other"
    assert list(config_path.parent.iterdir()) == [config_path]
    assert "Could not sync" in caplog.text


def _created_by_another_user(mocker, field=stat.ST_UID):
    """Report new regular files with a user or group other than the real one"""
    fstat = os.fstat

    def other_owner(fd):
        result = fstat(fd)
        if not stat.S_ISREG(result.st_mode):
            return result
        fields = list(result)
        fields[field] += 1
        return os.stat_result(fields)

    mocker.patch("sunbeam.core.deployments.os.fstat", side_effect=other_owner)
    return fstat


@pytest.mark.parametrize("field", [stat.ST_UID, stat.ST_GID])
def test_write_keeps_owner_of_existing_file(config_path, mocker, field):
    config = DeploymentsConfig.load(config_path)
    existing = config_path.stat()
    fstat = _created_by_another_user(mocker, field)
    changed = []

    def record_fchown(fd, uid, gid):
        changed.append((fstat(fd).st_ino, uid, gid))

    mocker.patch("sunbeam.core.deployments.os.fchown", side_effect=record_fchown)

    config.write()

    # Only the differing id is set, on the file that replaces the existing one
    written = config_path.stat()
    assert written.st_ino != existing.st_ino
    expected = {
        stat.ST_UID: (existing.st_uid, -1),
        stat.ST_GID: (-1, existing.st_gid),
    }
    assert changed == [(written.st_ino, *expected[field])]


@pytest.mark.parametrize("error", [errno.EPERM, errno.EOPNOTSUPP])
def test_write_succeeds_when_owner_cannot_be_kept(config_path, mocker, error):
    config = DeploymentsConfig.load(config_path)
    config.active = "new"
    _created_by_another_user(mocker)
    fchown = mocker.patch(
        "sunbeam.core.deployments.os.fchown",
        side_effect=OSError(error, os.strerror(error)),
    )

    config.write()

    fchown.assert_called_once()
    assert DeploymentsConfig.load(config_path).active == "new"


def test_write_preserves_existing_config_when_owner_change_fails(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    config.active = "other"
    _created_by_another_user(mocker)
    mocker.patch(
        "sunbeam.core.deployments.os.fchown",
        side_effect=OSError(errno.EIO, os.strerror(errno.EIO)),
    )

    with pytest.raises(OSError) as excinfo:
        config.write()

    assert excinfo.value.errno == errno.EIO
    assert config_path.read_text() == ORIGINAL
    assert list(config_path.parent.iterdir()) == [config_path]


def test_write_does_not_change_owner_that_already_matches(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    fchown = mocker.patch("sunbeam.core.deployments.os.fchown")

    config.write()

    fchown.assert_not_called()


def test_write_creates_missing_config_as_current_user(config_path, mocker):
    config = DeploymentsConfig.load(config_path)
    config_path.unlink()
    _created_by_another_user(mocker)
    fchown = mocker.patch("sunbeam.core.deployments.os.fchown")

    config.write()

    fchown.assert_not_called()
    assert config_path.stat().st_mode & 0o777 == 0o600
