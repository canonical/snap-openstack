# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for encrypted compute-storage command plans."""

from unittest import mock

import pytest
from click.testing import CliRunner

from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.provider.local import commands as local_commands
from sunbeam.provider.local.steps import LocalConfigureEncryptedStorageStep
from sunbeam.provider.maas import commands as maas_commands
from sunbeam.provider.maas.steps import MaasConfigureEncryptedStorageStep
from sunbeam.steps.hypervisor import DeployVaultlockerStep

NODE = "compute-1.example.com"


@pytest.mark.parametrize(
    "commands,uses_config,enrollment_step",
    [
        (local_commands, True, LocalConfigureEncryptedStorageStep),
        (local_commands, False, LocalConfigureEncryptedStorageStep),
        (maas_commands, True, MaasConfigureEncryptedStorageStep),
    ],
    ids=["local-config", "local-interactive", "maas-config"],
)
def test_compute_storage_deploys_vaultlocker_before_enrollment(
    commands, uses_config, enrollment_step, tmp_path
):
    """Both providers put deployment before the step that grants the secret."""
    deployment = mock.MagicMock()
    deployment.get_client.return_value.cluster.get_node_info.return_value = {
        "role": ["compute"]
    }
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

    with (
        mock.patch.object(commands, "load_compute_storage_config", return_value=config),
        mock.patch.object(commands, "run_preflight_checks"),
        mock.patch.object(commands, "run_plan") as run_plan,
        mock.patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value=NODE),
        mock.patch("sunbeam.provider.maas.commands.JujuHelper"),
    ):
        args = ["--config", str(config_path)] if uses_config else []
        result = CliRunner().invoke(
            commands.configure_compute_storage, args, obj=deployment
        )

    assert result.exit_code == 0, result.output
    plan = run_plan.call_args.args[0]
    assert [type(step) for step in plan] == [DeployVaultlockerStep, enrollment_step]
    assert plan[0].vault_offer_url == (config.vault_offer_url if uses_config else None)
    assert plan[0].target_nodes == [NODE]
    assert plan[1].config is (config if uses_config else None)
    assert f"Encrypted compute storage configuration completed on {NODE}." in (
        result.output
    )


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
