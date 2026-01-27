# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Functional tests for Sunbeam features.

These tests connect to an existing Sunbeam cluster and test feature
enablement/disablement lifecycle.
"""

import logging

import pytest

from .features.caas import CaaSTest
from .features.dns import DnsTest
from .features.images_sync import ImagesSyncTest
from .features.instance_recovery import InstanceRecoveryTest
from .features.loadbalancer import LoadbalancerTest
from .features.observability import ObservabilityTest
from .features.orchestration import OrchestrationTest
from .features.resource_optimization import ResourceOptimizationTest
from .features.shared_filesystem import SharedFilesystemTest
from .features.telemetry import TelemetryTest
from .features.tls import TlsCaTest
from .features.vault import VaultTest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.functional
def test_instance_recovery(sunbeam_client, juju_client, test_config):
    """Test instance-recovery feature lifecycle (enable/disable with verification)."""
    feature_test = InstanceRecoveryTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Instance recovery feature test failed"


@pytest.mark.functional
def test_caas(sunbeam_client, juju_client, test_config):
    """Test caas feature lifecycle (enable/disable only)."""
    feature_test = CaaSTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "CaaS feature test failed"


@pytest.mark.functional
def test_dns(sunbeam_client, juju_client, test_config):
    """Test dns feature lifecycle (enable/disable only)."""
    feature_test = DnsTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "DNS feature test failed"


@pytest.mark.functional
def test_images_sync(sunbeam_client, juju_client, test_config):
    """Test images-sync feature lifecycle (enable/disable only)."""
    feature_test = ImagesSyncTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Images-sync feature test failed"


@pytest.mark.functional
def test_loadbalancer(sunbeam_client, juju_client, test_config):
    """Test loadbalancer feature lifecycle (enable/disable only)."""
    feature_test = LoadbalancerTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Loadbalancer feature test failed"


@pytest.mark.functional
def test_orchestration(sunbeam_client, juju_client, test_config):
    """Test orchestration feature lifecycle (enable/disable only)."""
    feature_test = OrchestrationTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Orchestration feature test failed"


@pytest.mark.functional
def test_resource_optimization(sunbeam_client, juju_client, test_config):
    """Test resource-optimization feature lifecycle (enable/disable only)."""
    feature_test = ResourceOptimizationTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), (
        "Resource-optimization feature test failed"
    )


@pytest.mark.functional
def test_shared_filesystem(sunbeam_client, juju_client, test_config):
    """Test shared-filesystem feature lifecycle (enable/disable only)."""
    feature_test = SharedFilesystemTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Shared-filesystem feature test failed"


@pytest.mark.functional
def test_telemetry(sunbeam_client, juju_client, test_config):
    """Test telemetry feature lifecycle (enable/disable only)."""
    feature_test = TelemetryTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Telemetry feature test failed"


@pytest.mark.functional
def test_observability(sunbeam_client, juju_client, test_config):
    """Test observability feature lifecycle (enable/disable only)."""
    feature_test = ObservabilityTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Observability feature test failed"


@pytest.mark.functional
def test_maintenance(sunbeam_client, juju_client, test_config):
    """Placeholder for maintenance feature test (not yet enabled)."""
    pytest.skip("maintenance feature test not yet enabled in CI")


@pytest.mark.functional
def test_pro(sunbeam_client, juju_client, test_config):
    """Placeholder for pro feature test (not yet enabled)."""
    pytest.skip("pro feature test not yet enabled in CI")


@pytest.mark.functional
def test_tls_ca(sunbeam_client, juju_client, test_config):
    """Test TLS CA mode lifecycle (enable/disable with verification)."""
    feature_test = TlsCaTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "TLS CA feature test failed"


@pytest.mark.functional
def test_vault(sunbeam_client, juju_client, test_config):
    """Test vault feature lifecycle (enable/disable only)."""
    feature_test = VaultTest(sunbeam_client, juju_client, test_config)
    assert feature_test.run_full_lifecycle(), "Vault feature test failed"
