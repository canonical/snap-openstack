# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import click
import pytest

import sunbeam.core.questions
import sunbeam.features.interface.utils
import sunbeam.features.tls.ca as ca
import sunbeam.features.tls.common as tls
import sunbeam.features.tls.vault as vault
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ActionFailedException, LeaderNotFoundException


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.core.questions, "load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch.object(sunbeam.core.questions, "write_answers") as p:
        yield p


@pytest.fixture()
def question_bank():
    with patch.object(sunbeam.core.questions, "QuestionBank") as p:
        yield p


@pytest.fixture()
def get_subject_from_csr():
    with patch.object(ca, "get_subject_from_csr") as p:
        yield p


@pytest.fixture()
def is_certificate_valid():
    with patch.object(ca, "is_certificate_valid") as p:
        yield p


@pytest.fixture()
def get_outstanding():
    with patch.object(
        vault,
        "get_outstanding_certificate_requests",
    ) as p:
        yield p


@pytest.fixture()
def vault_get_subject_from_csr():
    """Patch the Vault version of get_subject_from_csr."""
    with patch.object(vault, "get_subject_from_csr") as p:
        yield p


@pytest.fixture()
def vault_is_certificate_valid():
    """Patch the Vault version of is_certificate_valid."""
    with patch.object(vault, "is_certificate_valid") as p:
        yield p


class TestAddCACertsToKeystoneStep:
    def test_is_skip(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0}
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_cabundle_already_distributed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0, name: "fake-ca-cert"}
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_action_failed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "action failed..."

    def test_is_skip_when_leader_not_found(self, jhelper):
        name = "cabundle"
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.is_skip()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "not able to get leader..."

    def test_run(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0}
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_when_action_failed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "action failed..."

    def test_run_when_leader_not_found(self, jhelper):
        name = "cabundle"
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = tls.AddCACertsToKeystoneStep(jhelper, name, "fake-cert", "fake-chain")
        result = step.run()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "not able to get leader..."


class TestRemoveCACertsFromKeystoneStep:
    def test_is_skip(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0, name: "fake-ca-cert"}
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_cabundle_not_distributed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0}
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_action_failed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.is_skip()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "action failed..."

    def test_is_skip_when_leader_not_found(self, jhelper):
        name = "cabundle"
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.is_skip()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "not able to get leader..."

    def test_run(self, jhelper):
        name = "cabundle"
        jhelper.run_action.return_value = {"return-code": 0}
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_when_action_failed(self, jhelper):
        name = "cabundle"
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.run()

        assert jhelper.run_action.call_count == 2
        assert result.result_type == ResultType.FAILED
        assert result.message == "action failed..."

    def test_run_when_action_with_compatible_name_succeeds(self, jhelper):
        name = "cabundle"
        feature_key = "ca.bundle"
        jhelper.run_action.side_effect = [
            ActionFailedException("action failed..."),
            {"return-code": 0},
        ]
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, feature_key)
        result = step.run()

        assert jhelper.run_action.call_count == 2
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_leader_not_found(self, jhelper):
        name = "cabundle"
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = tls.RemoveCACertsFromKeystoneStep(jhelper, name, name)
        result = step.run()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "not able to get leader..."


class TestConfigureCAStep:
    def test_prompt(
        self,
        cclient,
        jhelper,
        question_bank,
        load_answers,
        write_answers,
        get_subject_from_csr,
        is_certificate_valid,
    ):
        certs_to_process = [
            {
                "unit_name": "traefik/0",
                "csr": "fake-csr",
                "relation_id": 1,
            }
        ]
        jhelper.run_action.return_value = {
            "return-code": 0,
            "result": json.dumps(certs_to_process),
        }
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_called_once()
        write_answers.assert_called_once()

    def test_prompt_with_no_certs_to_process(
        self,
        cclient,
        jhelper,
        question_bank,
        load_answers,
        write_answers,
    ):
        certs_to_process = []
        jhelper.run_action.return_value = {
            "return-code": 0,
            "result": json.dumps(certs_to_process),
        }
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_not_called()
        write_answers.assert_not_called()

    def test_prompt_with_invalid_csr(
        self,
        cclient,
        jhelper,
        question_bank,
        load_answers,
        write_answers,
        get_subject_from_csr,
        is_certificate_valid,
    ):
        certs_to_process = [
            {
                "unit_name": "traefik/0",
                "csr": "invalid-csr",
                "relation_id": 1,
            }
        ]
        # invalid csr and so subject is None
        get_subject_from_csr.return_value = None
        jhelper.run_action.return_value = {
            "return-code": 0,
            "result": json.dumps(certs_to_process),
        }
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        with pytest.raises(click.ClickException):
            step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_called_once()
        write_answers.assert_not_called()

    def test_prompt_with_invalid_certificate(
        self,
        cclient,
        jhelper,
        question_bank,
        load_answers,
        write_answers,
        get_subject_from_csr,
        is_certificate_valid,
    ):
        certs_to_process = [
            {
                "unit_name": "traefik/0",
                "csr": "invalid-csr",
                "relation_id": 1,
            }
        ]
        # invalid certificate
        is_certificate_valid.return_value = False
        jhelper.run_action.return_value = {
            "return-code": 0,
            "result": json.dumps(certs_to_process),
        }
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        with pytest.raises(click.ClickException):
            step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_called_once()
        write_answers.assert_not_called()

    def test_prompt_when_action_returns_failed_return_code(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
    ):
        jhelper.run_action.return_value = {"return-code": 2}
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        with pytest.raises(click.ClickException):
            step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_not_called()
        write_answers.assert_not_called()

    def test_prompt_when_action_failed(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
    ):
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        with pytest.raises(ActionFailedException):
            step.prompt()

        jhelper.run_action.assert_called_once()
        load_answers.assert_not_called()
        write_answers.assert_not_called()

    def test_prompt_when_leader_not_found(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
    ):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        with pytest.raises(LeaderNotFoundException):
            step.prompt()

        jhelper.run_action.assert_not_called()
        load_answers.assert_not_called()
        write_answers.assert_not_called()

    def test_run(self, cclient, jhelper):
        jhelper.run_action.return_value = {"return-code": 0}
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.process_certs = {
            "subject1": {
                "app": "traefik",
                "unit": "traefik/0",
                "relation_id": 1,
                "csr": "fake-csr",
                "certificate": "fake-cert",
            }
        }

        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_no_certs_to_process(self, cclient, jhelper):
        jhelper.run_action.return_value = {"return-code": 0}
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        result = step.run()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_action_returns_failed_return_code(self, jhelper):
        jhelper.run_action.return_value = {"return-code": 2}
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.process_certs = {
            "subject1": {
                "app": "traefik",
                "unit": "traefik/0",
                "relation_id": 1,
                "csr": "fake-csr",
                "certificate": "fake-cert",
            }
        }
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED

    def test_run_when_action_failed(self, jhelper):
        jhelper.run_action.side_effect = ActionFailedException("action failed...")
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.process_certs = {
            "subject1": {
                "app": "traefik",
                "unit": "traefik/0",
                "relation_id": 1,
                "csr": "fake-csr",
                "certificate": "fake-cert",
            }
        }
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "action failed..."

    def test_run_when_leader_not_found(self, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "not able to get leader..."
        )
        step = ca.ConfigureCAStep(cclient, jhelper, "fake-cert", "fake-chain")
        step.process_certs = {
            "subject1": {
                "app": "traefik",
                "unit": "traefik/0",
                "relation_id": 1,
                "csr": "fake-csr",
                "certificate": "fake-cert",
            }
        }
        result = step.run()

        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "not able to get leader..."


class TestConfigureVaultCAStepPrompt:
    def test_prompt_normal(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
        question_bank,
        vault_get_subject_from_csr,
        vault_is_certificate_valid,
        get_outstanding,
    ):
        # one outstanding CSR
        rec = {"unit_name": "app/0", "csr": "fake-csr", "relation_id": 42}
        get_outstanding.return_value = {"return-code": 0, "result": json.dumps([rec])}

        vault_get_subject_from_csr.return_value = "subj"
        vault_is_certificate_valid.return_value = True

        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        step.prompt()

        get_outstanding.assert_called_once_with(step.app, step.model, step.jhelper)
        load_answers.assert_called_once_with(cclient, step._CONFIG)
        write_answers.assert_called_once()

    def test_prompt_no_certs(
        self, cclient, jhelper, load_answers, write_answers, get_outstanding
    ):
        get_outstanding.return_value = {"return-code": 0, "result": "[]"}
        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        step.prompt()
        load_answers.assert_not_called()
        write_answers.assert_not_called()

    def test_prompt_invalid_csr(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
        get_subject_from_csr,
        get_outstanding,
    ):
        rec = {"unit_name": "app/0", "csr": "bad", "relation_id": 1}
        get_outstanding.return_value = {"return-code": 0, "result": json.dumps([rec])}
        get_subject_from_csr.return_value = None

        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        with pytest.raises(click.ClickException) as exc:
            step.prompt()
        assert "Not a valid CSR" in str(exc.value)

        write_answers.assert_not_called()

    def test_prompt_invalid_cert(
        self,
        cclient,
        jhelper,
        load_answers,
        write_answers,
        question_bank,
        vault_get_subject_from_csr,
        vault_is_certificate_valid,
        get_outstanding,
    ):
        rec = {"unit_name": "app/0", "csr": "fake", "relation_id": 1}
        get_outstanding.return_value = {"return-code": 0, "result": json.dumps([rec])}
        vault_get_subject_from_csr.return_value = "subj"

        question_bank.return_value.certificate.ask.return_value = "invalid-cert"
        vault_is_certificate_valid.return_value = False

        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        with pytest.raises(click.ClickException) as exc:
            step.prompt()

        assert "Not a valid certificate" in str(exc.value)
        write_answers.assert_not_called()

    def test_prompt_error_return_code(
        self, cclient, jhelper, load_answers, write_answers, get_outstanding
    ):
        get_outstanding.return_value = {"return-code": 2}
        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        with pytest.raises(click.ClickException):
            step.prompt()

        load_answers.assert_not_called()

    def test_prompt_action_failed(
        self, cclient, jhelper, load_answers, write_answers, get_outstanding
    ):
        get_outstanding.side_effect = ActionFailedException("juju oops")
        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        with pytest.raises(ActionFailedException):
            step.prompt()

    def test_prompt_leader_not_found(
        self, cclient, jhelper, load_answers, write_answers, get_outstanding
    ):
        get_outstanding.side_effect = LeaderNotFoundException("no leader")
        step = vault.ConfigureVaultCAStep(cclient, jhelper, "fake-ca", "fake-chain")
        with pytest.raises(LeaderNotFoundException):
            step.prompt()


class TestConfigureVaultCAStepRun:
    def test_run_normal(self, jhelper):
        jhelper.get_leader_unit.return_value = "lead"
        jhelper.run_action.return_value = {"return-code": 0}
        step = vault.ConfigureVaultCAStep(None, jhelper, "fake-ca", "fake-chain")
        step.process_certs = {
            "subj": {
                "csr": "csr",
                "certificate": "cert",
            }
        }

        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_action.assert_called_once()

    def test_run_no_certs(self, jhelper):
        jhelper.get_leader_unit.return_value = "lead"
        step = vault.ConfigureVaultCAStep(None, jhelper, "fake-ca", "fake-chain")
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_not_called()

    def test_run_action_error_code(self, jhelper):
        jhelper.get_leader_unit.return_value = "lead"
        jhelper.run_action.return_value = {"return-code": 2}
        step = vault.ConfigureVaultCAStep(None, jhelper, "fake-ca", "fake-chain")
        step.process_certs = {"s": {"csr": "csr", "certificate": "cert"}}

        result = step.run()
        assert result.result_type == ResultType.FAILED

    def test_run_action_failed_exception(self, jhelper):
        jhelper.get_leader_unit.return_value = "lead"
        jhelper.run_action.side_effect = ActionFailedException("run oops")
        step = vault.ConfigureVaultCAStep(None, jhelper, "fake-ca", "fake-chain")
        step.process_certs = {"s": {"csr": "csr", "certificate": "cert"}}

        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert "run oops" in result.message

    def test_run_leader_not_found(self, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")
        step = vault.ConfigureVaultCAStep(None, jhelper, "fake-ca", "fake-chain")
        step.process_certs = {"s": {"csr": "csr", "certificate": "cert"}}

        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert "no leader" in result.message


class FakeUnit:
    def __init__(self, status, message):
        class WorkloadStatus:
            def __init__(self, current, message):
                self.current = current
                self.message = message

        self.workload_status = WorkloadStatus(status, message)
        self.workload_status_message = message


class FakeApp:
    def __init__(self, units):
        if isinstance(units, list):
            self.units = dict(enumerate(units))
        else:
            self.units = units


class TestVaultTlsFeatureIsActive:
    @patch(
        "sunbeam.features.tls.vault.VaultHelper.get_vault_status",
        return_value={"initialized": True, "sealed": False},
    )
    def test_active_status_short_circuits(self, mock_status):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"

        unit = FakeUnit(status="active", message="all good")
        app = FakeApp(units=[unit])
        jhelper.get_application.return_value = app

        feature = vault.VaultTlsFeature()
        assert feature.is_vault_application_active(jhelper) is True

    def test_no_units_raises(self):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.get_application.return_value = FakeApp(units=[])

        feature = vault.VaultTlsFeature()
        with pytest.raises(click.ClickException) as exc:
            feature.is_vault_application_active(jhelper)
        assert "has no units" in str(exc.value)

    @patch("sunbeam.features.tls.vault.VaultHelper.get_vault_status")
    def test_uninitialized_vault_raises(self, mock_status):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        fake_unit = FakeUnit(status="blocked", message="blocked…")
        jhelper.get_application.return_value = FakeApp(units=[fake_unit])
        mock_status.return_value = {"initialized": False, "sealed": False}

        feature = vault.VaultTlsFeature()
        with pytest.raises(click.ClickException) as exc:
            feature.is_vault_application_active(jhelper)
        assert "uninitialized" in str(exc.value)

    @patch("sunbeam.features.tls.vault.VaultHelper.get_vault_status")
    def test_sealed_vault_raises(self, mock_status):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        fake_unit = FakeUnit(status="blocked", message="blocked…")
        jhelper.get_application.return_value = FakeApp(units=[fake_unit])
        mock_status.return_value = {"initialized": True, "sealed": True}

        feature = vault.VaultTlsFeature()
        with pytest.raises(click.ClickException) as exc:
            feature.is_vault_application_active(jhelper)
        assert "sealed" in str(exc.value)

    def test_preseed_questions_content(self):
        """Test that preseed_questions_content returns a flat list of strings."""
        feature = vault.VaultTlsFeature()
        content = feature.preseed_questions_content()

        content = "\n".join(content)
        assert "certificates:" in content
        assert "TLS Certificates" in content
