# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures for storage backend tests."""

from pathlib import Path
from typing import Annotated
from unittest.mock import MagicMock

import pytest
from pydantic import Field

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField


class MockStorageConfig(StorageBackendConfig):
    """Mock configuration for testing."""

    required_field: Annotated[str, Field(description="A required field")]
    optional_field: Annotated[str | None, Field(description="An optional field")] = None
    secret_field: Annotated[
        str,
        Field(description="A secret field"),
        SecretDictField(field="secret-key"),
    ]
    int_field: Annotated[int | None, Field(description="An integer field")] = None


class MockStorageBackend(StorageBackendBase[MockStorageConfig]):
    """Mock storage backend for testing."""

    backend_type = "mock"
    display_name = "Mock Storage Backend"

    @property
    def charm_name(self) -> str:
        return "mock-charm"

    @property
    def charm_channel(self) -> str:
        return "latest/stable"

    def config_type(self) -> type[MockStorageConfig]:
        return MockStorageConfig


@pytest.fixture
def mock_backend():
    """Create a mock backend instance for testing."""
    return MockStorageBackend()


@pytest.fixture
def mock_deployment(tmp_path: Path):
    """Create a mock deployment object."""
    deployment = MagicMock()
    deployment.name = "test_deployment"
    deployment.plans_directory = tmp_path / "plans"
    deployment.plans_directory.mkdir(parents=True)
    deployment.openstack_machines_model = "openstack"
    deployment.juju_controller = "test-controller"

    # Mock get_space method
    deployment.get_space.return_value = "test-space"

    # Mock get_client
    mock_client = MagicMock()
    deployment.get_client.return_value = mock_client

    # Mock get_tfhelper
    mock_tfhelper = MagicMock()
    deployment.get_tfhelper.return_value = mock_tfhelper

    # Mock proxy settings
    deployment.get_proxy_settings.return_value = {}

    # Mock _get_juju_clusterd_env
    deployment._get_juju_clusterd_env.return_value = {}

    # Mock get_clusterd_http_address
    deployment.get_clusterd_http_address.return_value = "http://localhost:7000"

    # Mock _tfhelpers
    deployment._tfhelpers = {}

    return deployment


@pytest.fixture
def mock_jhelper():
    """Create a mock JujuHelper."""
    jhelper = MagicMock()
    jhelper.get_model_name_with_owner.return_value = "admin/openstack"

    # Mock model status
    mock_status = MagicMock()
    mock_status.apps = {}
    jhelper.get_model_status.return_value = mock_status

    # Mock get_model
    jhelper.get_model.return_value = {"model-uuid": "test-uuid"}

    return jhelper


@pytest.fixture
def mock_manifest():
    """Create a mock manifest."""
    manifest = MagicMock()
    manifest.storage.root = {}
    return manifest


@pytest.fixture
def mock_console():
    """Create a mock console."""
    return MagicMock()


@pytest.fixture
def terraform_plan_dir(tmp_path: Path):
    """Create a temporary terraform plan directory."""
    plan_dir = tmp_path / "etc" / "deploy-storage"
    plan_dir.mkdir(parents=True)

    # Create some dummy terraform files
    (plan_dir / "main.tf").write_text("# Terraform config")
    (plan_dir / "variables.tf").write_text("# Variables")

    return plan_dir


@pytest.fixture
def mock_click_context(mock_deployment, mock_manifest):
    """Create a mock Click context."""
    ctx = MagicMock()
    ctx.obj = mock_deployment
    mock_deployment.get_manifest.return_value = mock_manifest
    return ctx
