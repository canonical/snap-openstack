# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import stat
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

import sunbeam.core.questions
import sunbeam.provider.maas.steps as maas_steps
from sunbeam.core.common import ResultType
from sunbeam.core.deployment import CertPair
from sunbeam.core.juju import JujuSecretNotFound, LeaderNotFoundException
from sunbeam.provider.maas.deployment import MaasDeployment

from ...steps.test_configure import BaseTestUserQuestions


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


class TestMaasUserQuestions(BaseTestUserQuestions):
    __test__ = True

    @pytest.fixture(autouse=True)
    def setup_maas(self):
        self.maas_client = Mock()
        with patch("sunbeam.provider.maas.steps.maas_deployment") as p:
            self.maas_deployment = p
            yield

    def get_step(self):
        return maas_steps.MaasUserQuestions(self.cclient, self.maas_client)


CLUSTERD_CREDENTIALS = maas_steps.ClusterdCredentials(
    url="https://10.0.0.1:7000",
    certificate_authority="ca",
    certpair=CertPair(certificate="cert", private_key="key"),
)


class TestGetClusterdCredentials:
    @pytest.fixture
    def jhelper(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "sunbeam-clusterd/0"
        jhelper.run_action.return_value = {
            "url": "https://10.0.0.1:7000",
            "certificate-authority": "ca",
            "certificate": "cert",
            "private-key-secret": "secret:123",
        }
        jhelper.get_secret.return_value = {"private-key": "key"}
        return jhelper

    def test_get_clusterd_credentials(self, jhelper):
        credentials = maas_steps.get_clusterd_credentials(jhelper, "openstack-infra")
        assert credentials == CLUSTERD_CREDENTIALS
        jhelper.get_secret.assert_called_once_with("openstack-infra", "secret:123")

    def test_get_clusterd_credentials_no_leader(self, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")
        with pytest.raises(ValueError, match="no leader"):
            maas_steps.get_clusterd_credentials(jhelper, "openstack-infra")

    def test_get_clusterd_credentials_no_url(self, jhelper):
        del jhelper.run_action.return_value["url"]
        with pytest.raises(ValueError, match="Failed to retrieve clusterd url"):
            maas_steps.get_clusterd_credentials(jhelper, "openstack-infra")

    def test_get_clusterd_credentials_secret_not_found(self, jhelper):
        jhelper.get_secret.side_effect = JujuSecretNotFound("secret not found")
        with pytest.raises(ValueError, match="secret not found"):
            maas_steps.get_clusterd_credentials(jhelper, "openstack-infra")

    def test_get_clusterd_credentials_incomplete(self, jhelper):
        del jhelper.run_action.return_value["certificate"]
        with pytest.raises(ValueError, match="Failed to retrieve clusterd credentials"):
            maas_steps.get_clusterd_credentials(jhelper, "openstack-infra")


class TestMaasBackupClusterdCredentialsStep:
    @pytest.fixture
    def deployments_config(self, tmp_path):
        path = tmp_path / "deployments.yaml"
        path.write_text("active: test\n")
        path.chmod(0o600)
        deployments_config = Mock()
        deployments_config.path = path
        deployments_config.get_deployment.return_value = MaasDeployment(
            name="test",
            token="token",
            url="url",
            clusterd_address="https://10.0.0.1:7000",
            clusterd_certificate_authority="ca",
            clusterd_certpair=CertPair(certificate="old-cert", private_key="key"),
        )
        return deployments_config

    @pytest.fixture
    def get_clusterd_credentials(self, mocker):
        return mocker.patch.object(
            maas_steps, "get_clusterd_credentials", return_value=CLUSTERD_CREDENTIALS
        )

    def get_step(self, deployments_config):
        return maas_steps.MaasBackupClusterdCredentialsStep(
            Mock(), "test", deployments_config
        )

    def test_is_skip(self, deployments_config, get_clusterd_credentials, step_context):
        result = self.get_step(deployments_config).is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_not_maas_deployment(self, deployments_config, step_context):
        deployments_config.get_deployment.return_value = Mock()
        result = self.get_step(deployments_config).is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_no_clusterd_credentials(
        self, deployments_config, get_clusterd_credentials, step_context
    ):
        deployments_config.get_deployment.return_value = MaasDeployment(
            name="test", token="token", url="url"
        )
        result = self.get_step(deployments_config).is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED
        get_clusterd_credentials.assert_not_called()

    def test_is_skip_credentials_up_to_date(
        self, deployments_config, get_clusterd_credentials, step_context
    ):
        deployment = deployments_config.get_deployment.return_value
        deployment.clusterd_certpair = CLUSTERD_CREDENTIALS.certpair
        result = self.get_step(deployments_config).is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_credentials_unavailable(
        self, deployments_config, get_clusterd_credentials, step_context
    ):
        get_clusterd_credentials.side_effect = ValueError("no leader")
        result = self.get_step(deployments_config).is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "no leader"

    def test_run(self, deployments_config, step_context, mocker):
        mock_datetime = mocker.patch.object(maas_steps, "datetime")
        mock_datetime.now.return_value = datetime(2026, 10, 28, 17, 0, 0)

        result = self.get_step(deployments_config).run(step_context)

        backup_path = deployments_config.path.with_name(
            "deployments.yaml.bk.20261028170000"
        )
        assert result.result_type == ResultType.COMPLETED
        assert result.message == str(backup_path)
        assert backup_path.read_text() == "active: test\n"
        assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600

    def test_run_copy_fails(self, deployments_config, step_context):
        deployments_config.path.unlink()
        result = self.get_step(deployments_config).run(step_context)
        assert result.result_type == ResultType.FAILED
