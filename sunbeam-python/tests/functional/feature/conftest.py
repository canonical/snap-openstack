# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Pytest configuration and fixtures for Sunbeam feature functional tests."""

from pathlib import Path

import pytest
import yaml

from .utils.juju import JujuClient
from .utils.sunbeam import SunbeamClient


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--config",
        action="store",
        default="test_config.yaml",
        help="Path to test configuration file",
    )
    parser.addoption(
        "--features-disable-after",
        action="store",
        choices=["true", "false"],
        default=None,
        help=(
            "Override features.disable_after (true/false) from test_config.yaml "
            "without editing the file."
        ),
    )


@pytest.fixture(scope="session")
def test_config(request):
    """Load test configuration from YAML file."""
    config_path = request.config.getoption("--config")
    # Resolve relative to this feature functional directory
    config_file = Path(__file__).parent / config_path

    if not config_file.exists():
        msg = (
            f"Configuration file not found: {config_file}. "
            "Copy tests/functional/feature/test_config.yaml.example to "
            "tests/functional/feature/test_config.yaml and set sunbeam.deployment_name, juju.model."
        )
        pytest.skip(msg)

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    # Optional CLI override for disable-after behaviour.
    cli_disable_after = request.config.getoption("--features-disable-after")
    if cli_disable_after is not None:
        features_cfg = config.setdefault("features", {})
        features_cfg["disable_after"] = cli_disable_after == "true"

    return config


@pytest.fixture(scope="session")
def sunbeam_client(test_config):
    """Create Sunbeam client for test session."""
    deployment_name = test_config.get("sunbeam", {}).get("deployment_name")
    if not deployment_name:
        pytest.skip("deployment_name not configured in test_config.yaml")

    client = SunbeamClient(deployment_name)

    if not client.is_connected():
        pytest.skip(f"Cannot connect to Sunbeam deployment '{deployment_name}'.")

    return client


@pytest.fixture(scope="session")
def juju_client(test_config):
    """Create Juju client for test session."""
    model = test_config.get("juju", {}).get("model", "openstack")
    controller = test_config.get("juju", {}).get("controller")

    client = JujuClient(model=model, controller=controller)

    if not client.is_connected():
        pytest.skip(f"Cannot connect to Juju model '{model}'.")

    return client
