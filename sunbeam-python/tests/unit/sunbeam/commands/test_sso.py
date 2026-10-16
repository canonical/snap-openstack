# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import yaml
from click.testing import CliRunner

from sunbeam.commands.sso import (
    add_sso,
    purge_sso,
    remove_sso,
    set_saml_x509,
    update_sso,
)
from sunbeam.steps.openstack import ValidateInfraAppsStep
from sunbeam.steps.sso import RemoveExternalProviderStep, SetKeystoneSAMLCertAndKeyStep


def _deployment():
    deployment = MagicMock()
    deployment.get_tfhelper.return_value = MagicMock(plan="openstack-plan")
    deployment.get_manifest.return_value = MagicMock()
    return deployment


def _gate_calls(mock_run_plan):
    """Indexes of run_plan calls that carry the INFRA_APPS gate."""
    return [
        i
        for i, call in enumerate(mock_run_plan.call_args_list)
        if call.args
        and any(isinstance(step, ValidateInfraAppsStep) for step in call.args[0])
    ]


class TestSsoInfraAppsGate:
    """SSO flows that reapply the openstack plan run the gate first."""

    def _invoke(self, cmd, args, deployment):
        result = CliRunner().invoke(cmd, args, obj=deployment)
        assert result.exit_code == 0, result.output
        return result

    def test_add_external_provider_gates_before_apply(self, mocker, tmp_path):
        """Non-canonical providers reapply the whole plan: gate first."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch("sunbeam.commands.sso.safe_get_sso_config", return_value={})
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.safe_dump({"domain": "example.com"}))

        self._invoke(
            add_sso,
            ["google", "openid", "prov1", "--config", str(cfg_file)],
            deployment,
        )

        gate_calls = _gate_calls(mock_run_plan)
        assert gate_calls == [0], "the gate must be the first run_plan call"
        # The SSO steps apply exactly the stored tfvars: the gate plans
        # against those, not a manifest merge its apply never performs.
        gate_plan = mock_run_plan.call_args_list[0].args[0]
        assert gate_plan[1].manifest is None

    def test_add_canonical_provider_is_not_gated(self, mocker, tmp_path):
        """Canonical providers are SAAS-only and never touch the plan."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch("sunbeam.commands.sso.safe_get_sso_config", return_value={})
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.safe_dump({"oauth_offer": "offer-url"}))

        self._invoke(
            add_sso,
            ["canonical", "openid", "prov1", "--config", str(cfg_file)],
            deployment,
        )

        assert _gate_calls(mock_run_plan) == []

    def test_remove_external_provider_gates_first(self, mocker):
        """Removing an external provider reapplies the plan: gate first."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.safe_get_sso_config",
            return_value={"openid": {"prov1": {"provider_type": "google"}}},
        )

        self._invoke(remove_sso, ["prov1", "openid", "--yes-i-mean-it"], deployment)

        gate_calls = _gate_calls(mock_run_plan)
        assert gate_calls == [0]
        teardown = mock_run_plan.call_args_list[-1].args[0]
        assert any(isinstance(step, RemoveExternalProviderStep) for step in teardown)

    def test_remove_canonical_provider_is_not_gated(self, mocker):
        """Canonical removal only deletes SAAS applications: no gate."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.safe_get_sso_config",
            return_value={
                "openid": {"prov1": {"provider_type": "canonical"}},
                "prov1": {"provider_type": "canonical"},
            },
        )

        self._invoke(remove_sso, ["prov1", "openid", "--yes-i-mean-it"], deployment)

        assert _gate_calls(mock_run_plan) == []

    def test_update_gates_before_apply(self, mocker, tmp_path):
        """Updating a provider reapplies the whole plan: gate first."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.read_config",
            return_value={"openid": {"prov1": {"provider_type": "google"}}},
        )
        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text(
            yaml.safe_dump({"client_id": "id", "client_secret": "secret"})
        )

        self._invoke(
            update_sso,
            ["prov1", "openid", "--secrets-file", str(secrets_file)],
            deployment,
        )

        assert _gate_calls(mock_run_plan) == [0]

    def test_update_canonical_provider_is_not_gated(self, mocker, tmp_path):
        """Canonical updates are skipped by the step: no wasted gate."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.read_config",
            return_value={"openid": {"prov1": {"provider_type": "canonical"}}},
        )
        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text(
            yaml.safe_dump({"client_id": "id", "client_secret": "secret"})
        )

        self._invoke(
            update_sso,
            ["prov1", "openid", "--secrets-file", str(secrets_file)],
            deployment,
        )

        assert _gate_calls(mock_run_plan) == []

    def test_purge_with_external_provider_gates_first(self, mocker):
        """Purging external providers reapplies the plan: gate first."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.safe_get_sso_config",
            return_value={"openid": {"prov1": {"provider_type": "google"}}},
        )

        self._invoke(purge_sso, ["--yes-i-mean-it"], deployment)

        gate_calls = _gate_calls(mock_run_plan)
        assert gate_calls == [0]
        teardown = mock_run_plan.call_args_list[-1].args[0]
        assert any(isinstance(step, RemoveExternalProviderStep) for step in teardown)

    def test_purge_with_only_canonical_providers_is_not_gated(self, mocker):
        """A canonical-only purge never touches the plan: no gate."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")
        mocker.patch(
            "sunbeam.commands.sso.safe_get_sso_config",
            return_value={"openid": {"prov1": {"provider_type": "canonical"}}},
        )

        self._invoke(purge_sso, ["--yes-i-mean-it"], deployment)

        assert _gate_calls(mock_run_plan) == []

    def test_set_saml_x509_gates_first(self, mocker):
        """Setting the SAML certificate reapplies the plan: gate first."""
        deployment = _deployment()
        mock_run_plan = mocker.patch("sunbeam.commands.sso.run_plan")
        mocker.patch("sunbeam.commands.sso.JujuHelper")
        mocker.patch("sunbeam.commands.sso.run_preflight_checks")

        self._invoke(set_saml_x509, ["cert", "key"], deployment)

        assert _gate_calls(mock_run_plan) == [0]
        apply_plan = mock_run_plan.call_args_list[-1].args[0]
        assert any(
            isinstance(step, SetKeystoneSAMLCertAndKeyStep) for step in apply_plan
        )
