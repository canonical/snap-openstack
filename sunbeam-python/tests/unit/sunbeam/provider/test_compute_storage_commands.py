# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for encrypted compute-storage command plans."""

from unittest import mock

import pytest
from click.testing import CliRunner

from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.provider.local import commands as local_commands
from sunbeam.provider.local.steps import LocalConfigureEncryptedStorageStep
from sunbeam.provider.maas import commands as maas_commands
from sunbeam.provider.maas.steps import MaasConfigureEncryptedStorageStep
from sunbeam.steps.hypervisor import DeployVaultlockerStep

NODE = "compute-1.example.com"


@pytest.mark.parametrize(
    ("commands", "enrollment_step"),
    [
        (local_commands, LocalConfigureEncryptedStorageStep),
        (maas_commands, MaasConfigureEncryptedStorageStep),
    ],
    ids=["local", "maas"],
)
def test_valid_config_builds_enrollment_plan(commands, enrollment_step, tmp_path):
    """Both providers initialize Terraform before deploying and enrolling."""
    config = ComputeStorageConfig(
        vault_offer_url="vault.vault-kv",
        nodes={
            NODE: NodeStorageConfig(
                target="/dev/disk/by-id/test", existing_key_secret_id="secret:abc"
            )
        },
    )
    config_path = tmp_path / "compute-storage.yaml"
    config_path.touch()
    deployment = mock.MagicMock()
    deployment.get_client.return_value.cluster.get_node_info.return_value = {
        "role": ["compute"]
    }
    with (
        mock.patch.object(commands, "load_compute_storage_config", return_value=config),
        mock.patch.object(commands, "run_preflight_checks"),
        mock.patch.object(commands, "run_plan") as run_plan,
        mock.patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value=NODE),
        mock.patch("sunbeam.provider.maas.commands.JujuHelper"),
    ):
        result = CliRunner().invoke(
            commands.configure_compute_storage,
            ["--config", str(config_path)],
            obj=deployment,
        )

    assert result.exit_code == 0, result.output
    plan = run_plan.call_args.args[0]
    assert [type(step) for step in plan] == [
        TerraformInitStep,
        DeployVaultlockerStep,
        enrollment_step,
    ]
    assert plan[1].target_nodes == [NODE]
    if commands is local_commands:
        assert plan[2].target == "/dev/disk/by-id/test"
        assert plan[2].existing_key_secret_id == "secret:abc"
        assert not plan[2].has_prompts()
    else:
        assert plan[2].config is config


@pytest.mark.parametrize(
    "commands", [local_commands, maas_commands], ids=["local", "maas"]
)
def test_compute_storage_reports_config_mistake(commands, tmp_path):
    """A missing config field should be reported as an input mistake."""
    deployment = mock.MagicMock()
    deployment.get_client.return_value.cluster.get_node_info.return_value = {
        "role": ["compute"]
    }
    config_path = tmp_path / "compute-storage.yaml"
    config_path.write_text(
        "vault_offer_url: vault.vault-kv\n"
        f"nodes:\n  {NODE}:\n    target: /dev/disk/by-id/test\n"
    )

    with (
        mock.patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value=NODE),
        mock.patch.object(commands, "run_plan") as run_plan,
    ):
        result = CliRunner().invoke(
            commands.configure_compute_storage,
            ["--config", str(config_path)],
            obj=deployment,
        )

    assert result.exit_code == 1
    assert "Invalid compute-storage config" in result.output
    assert "existing_key_secret_id" in result.output
    assert "An unexpected error" not in result.output
    run_plan.assert_not_called()


def test_maas_compute_storage_rejects_config_without_hosts(tmp_path):
    """Reject an empty MAAS host list before logging into Juju."""
    config_path = tmp_path / "compute-storage.yaml"
    config_path.write_text("vault_offer_url: vault.vault-kv\n")

    with (
        mock.patch.object(maas_commands, "run_preflight_checks") as preflight,
        mock.patch.object(maas_commands, "run_plan") as run_plan,
    ):
        result = CliRunner().invoke(
            maas_commands.configure_compute_storage,
            ["--config", str(config_path)],
            obj=mock.MagicMock(),
        )

    assert result.exit_code == 1
    assert "at least one compute host is required" in result.output
    preflight.assert_not_called()
    run_plan.assert_not_called()


def test_local_compute_storage_rejects_config_without_local_host(tmp_path):
    config_path = tmp_path / "compute-storage.yaml"
    config_path.write_text(
        "vault_offer_url: vault.vault-kv\n"
        "nodes:\n"
        "  other.example.com:\n"
        "    target: /dev/disk/by-id/test\n"
        "    existing_key_secret_id: secret:abc\n"
    )
    deployment = mock.MagicMock()
    deployment.get_client.return_value.cluster.get_node_info.return_value = {
        "role": ["compute"]
    }
    with (
        mock.patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value=NODE),
        mock.patch.object(local_commands, "run_preflight_checks") as preflight,
        mock.patch.object(local_commands, "run_plan") as run_plan,
    ):
        result = CliRunner().invoke(
            local_commands.configure_compute_storage,
            ["--config", str(config_path)],
            obj=deployment,
        )

    assert result.exit_code == 1
    assert f"No encrypted compute-storage configuration for {NODE}" in result.output
    preflight.assert_not_called()
    run_plan.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_maas_compute_storage_rejects_invalid_host_before_plan(tmp_path, missing):
    config_path = tmp_path / "compute-storage.yaml"
    config_path.write_text(
        "vault_offer_url: vault.vault-kv\n"
        "nodes:\n"
        f"  {NODE}:\n"
        "    target: /dev/disk/by-id/test\n"
        "    existing_key_secret_id: secret:abc\n"
    )
    deployment = mock.MagicMock()
    if missing:
        from sunbeam.clusterd.service import NodeNotExistInClusterException

        deployment.get_client.return_value.cluster.get_node_info.side_effect = (
            NodeNotExistInClusterException()
        )
    else:
        deployment.get_client.return_value.cluster.get_node_info.return_value = {
            "role": ["control"]
        }
    with (
        mock.patch.object(maas_commands, "run_preflight_checks") as preflight,
        mock.patch.object(maas_commands, "run_plan") as run_plan,
    ):
        result = CliRunner().invoke(
            maas_commands.configure_compute_storage,
            ["--config", str(config_path)],
            obj=deployment,
        )

    assert result.exit_code == 1
    assert NODE in result.output
    assert ("not in the cluster" if missing else "compute role") in result.output
    preflight.assert_not_called()
    run_plan.assert_not_called()
