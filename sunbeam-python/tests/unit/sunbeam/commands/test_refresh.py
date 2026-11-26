# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import click
import pytest
import yaml
from click.testing import CliRunner

from sunbeam.clusterd.service import ManifestItemNotFoundException
from sunbeam.commands.refresh import _stored_manifest_risk, refresh
from sunbeam.core.common import RiskLevel
from sunbeam.core.manifest import AddManifestStep
from sunbeam.steps.ingress import ReapplyIngressTerraformPlanStep
from sunbeam.steps.openstack import ValidateInfraAppsStep
from sunbeam.steps.vault import VaultCharmUpgradeStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest_data(risk: str) -> str:
    """Return a YAML manifest string where all charms use *risk*."""
    data = {
        "core": {
            "software": {
                "charms": {
                    "nova-k8s": {"channel": f"2024.1/{risk}"},
                    "neutron-k8s": {"channel": f"2024.1/{risk}"},
                    "keystone-k8s": {"channel": f"2024.1/{risk}"},
                    "glance-k8s": {"channel": f"2024.1/{risk}"},
                    "microceph": {"channel": f"squid/{risk}"},
                }
            }
        }
    }
    return yaml.safe_dump(data)


def _make_mixed_manifest_data(majority_risk: str, minority_risk: str) -> str:
    """Return a YAML manifest string where most charms use *majority_risk*.

    One charm is overridden to *minority_risk* to simulate a user customisation.
    """
    data = {
        "core": {
            "software": {
                "charms": {
                    "nova-k8s": {"channel": f"2024.1/{majority_risk}"},
                    "neutron-k8s": {"channel": f"2024.1/{majority_risk}"},
                    "keystone-k8s": {"channel": f"2024.1/{majority_risk}"},
                    "glance-k8s": {"channel": f"2024.1/{majority_risk}"},
                    # User-customised single charm
                    "custom-charm": {"channel": f"latest/{minority_risk}"},
                }
            }
        }
    }
    return yaml.safe_dump(data)


# ---------------------------------------------------------------------------
# Tests for _stored_manifest_risk
# ---------------------------------------------------------------------------


class TestStoredManifestRisk:
    def _make_client(self, manifest_data: str | None):
        """Return a mock clusterd client.

        Pass *manifest_data* as the stored manifest YAML string, or
        raise ManifestItemNotFoundException when it is ``None``.
        """
        client = MagicMock()
        if manifest_data is None:
            client.cluster.get_latest_manifest.side_effect = (
                ManifestItemNotFoundException("no manifest")
            )
        else:
            client.cluster.get_latest_manifest.return_value = {"data": manifest_data}
        return client

    def test_no_stored_manifest_returns_none(self):
        """No manifest in clusterd → return None (fresh cluster)."""
        client = self._make_client(None)
        assert _stored_manifest_risk(client) is None

    def test_stable_manifest_returns_stable(self):
        """All charm channels at stable → dominant risk is stable."""
        client = self._make_client(_make_manifest_data("stable"))
        assert _stored_manifest_risk(client) == "stable"

    def test_beta_manifest_returns_beta(self):
        """All charm channels at beta → dominant risk is beta."""
        client = self._make_client(_make_manifest_data("beta"))
        assert _stored_manifest_risk(client) == "beta"

    def test_candidate_manifest_returns_candidate(self):
        """All charm channels at candidate → dominant risk is candidate."""
        client = self._make_client(_make_manifest_data("candidate"))
        assert _stored_manifest_risk(client) == "candidate"

    def test_edge_manifest_returns_edge(self):
        """All charm channels at edge → dominant risk is edge."""
        client = self._make_client(_make_manifest_data("edge"))
        assert _stored_manifest_risk(client) == "edge"

    def test_majority_risk_wins_with_one_custom_override(self):
        """User added one beta charm but the rest are stable → returns stable."""
        client = self._make_client(_make_mixed_manifest_data("stable", "beta"))
        assert _stored_manifest_risk(client) == "stable"

    def test_majority_risk_wins_beta_with_one_stable_override(self):
        """Most charms are beta; one user-set stable override → returns beta."""
        client = self._make_client(_make_mixed_manifest_data("beta", "stable"))
        assert _stored_manifest_risk(client) == "beta"

    def test_empty_charms_section_returns_none(self):
        """Manifest with no charms → returns None."""
        data = yaml.safe_dump({"core": {"software": {"charms": {}}}})
        client = self._make_client(data)
        assert _stored_manifest_risk(client) is None

    def test_missing_charms_section_returns_none(self):
        """Manifest with no software.charms key → returns None."""
        data = yaml.safe_dump({"core": {"software": {}}})
        client = self._make_client(data)
        assert _stored_manifest_risk(client) is None

    def test_channels_without_risk_component_returns_none(self):
        """Charm channels that have no recognisable risk → returns None."""
        data = yaml.safe_dump(
            {
                "core": {
                    "software": {
                        "charms": {
                            "nova-k8s": {"channel": "2024.1"},  # no risk part
                            "keystone-k8s": {"channel": ""},  # empty
                            "neutron-k8s": {},  # no channel key
                        }
                    }
                }
            }
        )
        client = self._make_client(data)
        assert _stored_manifest_risk(client) is None

    def test_empty_manifest_data_returns_none(self):
        """Stored manifest data is an empty string → returns None."""
        client = self._make_client("")
        assert _stored_manifest_risk(client) is None


# ---------------------------------------------------------------------------
# Tests for the refresh CLI command
# ---------------------------------------------------------------------------


def _make_runner_context(deployment_mock):
    """Return a CliRunner and an invocation kwargs dict that injects obj."""
    runner = CliRunner()
    return runner, {"obj": deployment_mock}


def _build_deployment(client_mock):
    deployment = MagicMock()
    deployment.get_client.return_value = client_mock
    deployment.get_manifest.return_value = MagicMock()
    return deployment


def _build_client(manifest_risk: str | None):
    """Build a clusterd client mock whose stored manifest has *manifest_risk*."""
    client = MagicMock()
    if manifest_risk is None:
        client.cluster.get_latest_manifest.side_effect = ManifestItemNotFoundException(
            "no manifest"
        )
    else:
        client.cluster.get_latest_manifest.return_value = {
            "data": _make_manifest_data(manifest_risk)
        }
    return client


@pytest.fixture(autouse=True)
def _patch_upgrade_coordinators(mocker):
    """Prevent real Juju / Terraform calls during refresh tests."""
    mocker.patch(
        "sunbeam.commands.refresh.LatestInChannelCoordinator",
        autospec=True,
    )
    mocker.patch(
        "sunbeam.commands.refresh.ChannelUpgradeCoordinator",
        autospec=True,
    )
    mocker.patch("sunbeam.commands.refresh.JujuHelper", autospec=True)
    mocker.patch("sunbeam.commands.refresh.run_preflight_checks")
    mocker.patch("sunbeam.commands.refresh.run_plan")


class TestRefreshWarning:
    """Tests for the snap-channel-change warning in the refresh command."""

    def _invoke(self, args, deployment, input=None):
        runner = CliRunner()
        return runner.invoke(refresh, args, obj=deployment, input=input)

    # -- helpers ---------------------------------------------------------

    def _deployment_stable_snap(self, client):
        """Deployment whose snap reports stable risk."""
        d = _build_deployment(client)
        return d

    # -- no warning cases ------------------------------------------------

    def test_no_warning_same_risk_stable(self, mocker):
        """Stable snap + stable stored manifest → no confirmation prompt."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.STABLE,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_no_warning_same_risk_beta(self, mocker):
        """Beta snap + beta stored manifest → no confirmation prompt."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("beta")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_no_warning_when_no_stored_manifest(self, mocker):
        """No manifest in clusterd (fresh cluster) → no confirmation prompt."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client(None)  # triggers ManifestItemNotFoundException
        deployment = _build_deployment(client)

        result = self._invoke([], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_no_warning_when_manifest_path_provided(self, mocker, tmp_path):
        """Explicit -m flag → skip channel-change check entirely."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)
        # AddManifestStep is called with the file; mock it away
        mocker.patch("sunbeam.commands.refresh.AddManifestStep")

        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("beta"))

        result = self._invoke(["-m", str(manifest_file)], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_no_warning_when_clear_manifest(self, mocker):
        """--clear-manifest flag → skip channel-change check entirely."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mocker.patch("sunbeam.commands.refresh.AddManifestStep")

        result = self._invoke(["--clear-manifest"], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_no_warning_on_exception_during_comparison(self, mocker):
        """If risk comparison raises an unexpected error, silently continue."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            side_effect=RuntimeError("unexpected"),
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment)

        # Should not abort; the exception is swallowed via LOG.debug
        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    # -- warning / abort cases -------------------------------------------

    def test_warning_shown_when_risk_changed_stable_to_beta(self, mocker):
        """Stable stored manifest + beta snap → confirmation prompt shown."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        # Answer 'y' so the command proceeds
        result = self._invoke([], deployment, input="y\n")

        assert "Continue anyway?" in result.output
        assert result.exit_code == 0

    def test_warning_shown_when_risk_changed_beta_to_stable(self, mocker):
        """Beta stored manifest + stable snap → confirmation prompt shown."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.STABLE,
        )
        client = _build_client("beta")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment, input="y\n")

        assert "Continue anyway?" in result.output
        assert result.exit_code == 0

    def test_abort_when_user_declines_confirmation(self, mocker):
        """User answers 'n' to the prompt → command is aborted."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment, input="n\n")

        assert "Continue anyway?" in result.output
        assert result.exit_code != 0

    def test_force_flag_bypasses_confirmation_when_channel_changed(self, mocker):
        """--force skips the prompt even when the snap channel has changed."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke(["--force"], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_force_flag_bypasses_without_interactive_input(self, mocker):
        """--force bypasses the prompt without requiring interactive input."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.BETA,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke(["--force"], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    def test_warning_shown_stable_to_candidate(self, mocker):
        """Stable stored manifest + candidate snap → confirmation prompt shown."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.CANDIDATE,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        result = self._invoke([], deployment, input="y\n")

        assert "Continue anyway?" in result.output
        assert result.exit_code == 0

    def test_user_custom_override_does_not_trigger_false_positive(self, mocker):
        """User added one beta charm to an otherwise stable manifest.

        The dominant risk is still stable, so no warning when snap is also stable.
        """
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.STABLE,
        )
        client = MagicMock()
        client.cluster.get_latest_manifest.return_value = {
            "data": _make_mixed_manifest_data("stable", "beta")
        }
        deployment = _build_deployment(client)

        result = self._invoke([], deployment)

        assert result.exit_code == 0
        assert "Continue anyway?" not in (result.output or "")

    # -- mutual-exclusion guard ------------------------------------------

    def test_error_when_manifest_and_clear_manifest_both_given(self, mocker, tmp_path):
        """Passing both -m and --clear-manifest raises a ClickException."""
        mocker.patch(
            "sunbeam.commands.refresh.infer_risk",
            return_value=RiskLevel.STABLE,
        )
        client = _build_client("stable")
        deployment = _build_deployment(client)

        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(
            ["--clear-manifest", "-m", str(manifest_file)], deployment
        )

        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()


# ---------------------------------------------------------------------------
# Manifest store ordering: validation must accept a manifest before it is
# stored as the cluster's latest manifest.
# ---------------------------------------------------------------------------


class TestRefreshManifestStoreOrdering:
    """Tests for when the user manifest is stored during refresh flows."""

    def _invoke(self, args, deployment):
        runner = CliRunner()
        return runner.invoke(refresh, args, obj=deployment)

    def _calls(self, mock_run_plan):
        """Return the step lists of every run_plan call, in order."""
        return [call.args[0] for call in mock_run_plan.call_args_list if call.args]

    def _first_call_with(self, calls, *step_classes):
        for i, steps in enumerate(calls):
            if any(isinstance(step, step_classes) for step in steps):
                return i
        raise AssertionError(f"No run_plan call with {step_classes}")

    def test_manifest_stored_after_infra_apps_gate(self, mocker, tmp_path):
        """Generic refresh: the gate validates the manifest before storing."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(["-m", str(manifest_file)], deployment)

        assert result.exit_code == 0
        calls = self._calls(mock_run_plan)
        gate_idx = self._first_call_with(calls, ValidateInfraAppsStep)
        store_idx = self._first_call_with(calls, AddManifestStep)
        assert gate_idx < store_idx, "gate must run before the manifest is stored"

    def test_upgrade_release_stores_manifest_without_gate(self, mocker, tmp_path):
        """Release upgrades are exempt from the gate and store upfront."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(
            ["--upgrade-release", "-m", str(manifest_file)], deployment
        )

        assert result.exit_code == 0
        calls = self._calls(mock_run_plan)
        assert not any(
            isinstance(step, ValidateInfraAppsStep) for steps in calls for step in steps
        ), "release upgrade must not run the INFRA_APPS gate"
        store_idx = self._first_call_with(calls, AddManifestStep)
        assert store_idx == 0, "manifest is stored upfront on release upgrades"

    def test_vault_stores_manifest_after_refresh(self, mocker, tmp_path):
        """Vault refresh: the manifest is stored only after the refresh."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(["vault", "-m", str(manifest_file)], deployment)

        assert result.exit_code == 0
        calls = self._calls(mock_run_plan)
        refresh_idx = self._first_call_with(calls, VaultCharmUpgradeStep)
        store_idx = self._first_call_with(calls, AddManifestStep)
        assert refresh_idx < store_idx

    def test_ingress_stores_manifest_after_refresh(self, mocker, tmp_path):
        """Ingress refresh: the manifest is stored only after the refresh."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(["ingress", "-m", str(manifest_file)], deployment)

        assert result.exit_code == 0
        calls = self._calls(mock_run_plan)
        refresh_idx = self._first_call_with(calls, ReapplyIngressTerraformPlanStep)
        store_idx = self._first_call_with(calls, AddManifestStep)
        assert refresh_idx < store_idx

    def test_mysql_stores_manifest_after_refresh(self, mocker, tmp_path):
        """MySQL refresh: the manifest is stored only after the refresh."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        order: list[str] = []
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        mock_run_plan.side_effect = lambda steps, *a, **k: (
            order.append("store")
            if any(isinstance(step, AddManifestStep) for step in steps)
            else None
        )
        coordinator = mocker.patch(
            "sunbeam.commands.refresh.MySQLInChannelUpgradeCoordinator",
            autospec=True,
        )
        coordinator.return_value.run_plan.side_effect = lambda *a, **k: order.append(
            "refresh"
        )
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(["mysql", "-m", str(manifest_file)], deployment)

        assert result.exit_code == 0, result.output
        assert order == ["refresh", "store"]

    def test_mysql_failure_does_not_store_manifest(self, mocker, tmp_path):
        """A rejected mysql refresh must not store the user manifest."""
        client = _build_client("stable")
        deployment = _build_deployment(client)
        mock_run_plan = mocker.patch("sunbeam.commands.refresh.run_plan")
        coordinator = mocker.patch(
            "sunbeam.commands.refresh.MySQLInChannelUpgradeCoordinator",
            autospec=True,
        )
        coordinator.return_value.run_plan.side_effect = click.ClickException(
            "Manifest has track values that require upgrades"
        )
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(_make_manifest_data("stable"))

        result = self._invoke(["mysql", "-m", str(manifest_file)], deployment)

        assert result.exit_code != 0
        assert not any(
            isinstance(step, AddManifestStep)
            for call in mock_run_plan.call_args_list
            if call.args
            for step in call.args[0]
        )
