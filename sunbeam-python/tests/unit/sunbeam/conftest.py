# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from snaphelpers import Snap, SnapConfig, SnapServices


@pytest.fixture(autouse=True)
def snap_env(tmp_path: Path, mocker):
    """Environment variables defined in the snap.

    This is primarily used to setup the snaphelpers bit.
    """
    snap_name = "sunbeam-test"
    real_home = tmp_path / "home/ubuntu"
    snap_user_common = real_home / f"snap/{snap_name}/common"
    snap_user_data = real_home / f"snap/{snap_name}/2"
    snap_path = tmp_path / f"snap/2/{snap_name}"
    snap_common = tmp_path / f"var/snap/{snap_name}/common"
    snap_data = tmp_path / f"var/snap/{snap_name}/2"
    env = {
        "SNAP": str(snap_path),
        "SNAP_COMMON": str(snap_common),
        "SNAP_DATA": str(snap_data),
        "SNAP_USER_COMMON": str(snap_user_common),
        "SNAP_USER_DATA": str(snap_user_data),
        "SNAP_REAL_HOME": str(real_home),
        "SNAP_INSTANCE_NAME": "",
        "SNAP_NAME": snap_name,
        "SNAP_REVISION": "2",
        "SNAP_VERSION": "1.2.3",
    }
    mocker.patch("os.environ", env)
    yield env


@pytest.fixture
def snap(snap_env):
    snap = Snap(environ=snap_env)
    snap.config = MagicMock(SnapConfig)
    snap.services = MagicMock(SnapServices)
    yield snap


@pytest.fixture
def run():
    with patch("subprocess.run") as p:
        yield p


@pytest.fixture
def check_call():
    with patch("subprocess.check_call") as p:
        yield p


@pytest.fixture
def check_output():
    with patch("subprocess.check_output") as p:
        yield p


@pytest.fixture
def environ():
    with patch("os.environ") as p:
        yield p


@pytest.fixture
def copytree():
    with patch("shutil.copytree") as p:
        yield p


@pytest.fixture(autouse=True)
def tenacity_patch(mocker):
    import tenacity

    mocker.patch("tenacity.wait_fixed", return_value=tenacity.wait_fixed(0))
    return tenacity


# Common test fixtures used across multiple test files
@pytest.fixture
def basic_client():
    """Basic client mock used by most test classes."""
    return Mock()


@pytest.fixture
def basic_deployment():
    """Basic deployment mock used by most test classes.

    Returns a MagicMock to provide maximum flexibility for test files.
    Individual test files can override this if they need specific behavior.
    """
    return MagicMock()


@pytest.fixture
def basic_jhelper():
    """Basic juju helper mock used by most test classes."""
    jhelper = Mock()
    jhelper.run_action.return_value = {}
    return jhelper


@pytest.fixture
def basic_tfhelper():
    """Basic terraform helper mock used by most test classes."""
    return Mock()


@pytest.fixture
def basic_manifest():
    """Basic manifest mock used by most test classes."""
    manifest = MagicMock()
    manifest.core.config.pci = None
    return manifest


@pytest.fixture
def test_model():
    """Standard test model name."""
    return "test-model"


@pytest.fixture
def test_name():
    """Standard test node name."""
    return "test-0"


@pytest.fixture
def test_namespace():
    """Standard test namespace."""
    return "test-namespace"


@pytest.fixture
def test_token():
    """Standard test token for testing."""
    return "TOKENFORTESTING"


@pytest.fixture
def snap_mock():
    """Mock for Snap object."""
    return Mock()


@pytest.fixture
def snap_patch(snap_mock):
    """Patch for sunbeam.core.k8s.Snap."""
    with patch("sunbeam.core.k8s.Snap", snap_mock) as mock:
        yield mock


@pytest.fixture
def read_config_patch():
    """Generic patch for read_config functions."""
    with patch("sunbeam.core.steps.read_config", return_value={}) as mock:
        yield mock


# Additional commonly used fixtures
@pytest.fixture
def cclient():
    """Cluster client mock."""
    return MagicMock()


@pytest.fixture
def deployment():
    """Deployment mock."""
    return MagicMock()


@pytest.fixture
def jhelper():
    """Juju helper mock."""
    return MagicMock()


@pytest.fixture
def tfhelper():
    """Terraform helper mock."""
    return MagicMock()


@pytest.fixture
def manifest():
    """Manifest mock."""
    return MagicMock()
