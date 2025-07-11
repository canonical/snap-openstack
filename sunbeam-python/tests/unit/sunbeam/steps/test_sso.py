# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.steps.sso import (
    AddCanonicalProviderStep,
    AddEntraProviderStep,
    AddGenericProviderStep,
    AddGoogleProviderStep,
    AddOktaProviderStep,
    RemoveExternalProviderStep,
    UpdateExternalProviderStep,
)


@pytest.fixture()
def config_store():
    return {}


@pytest.fixture()
def read_config(config_store):
    with patch("sunbeam.steps.sso.read_config") as mock_read:

        def side_effect(client, key):
            if key in config_store:
                return config_store[key]
            raise ConfigItemNotFoundException()

        mock_read.side_effect = side_effect
        yield mock_read


@pytest.fixture()
def update_config(config_store):
    with patch("sunbeam.steps.sso.update_config") as mock_update:

        def side_effect(client, key, value):
            config_store[key] = value

        mock_update.side_effect = side_effect
        yield mock_update


@pytest.fixture()
def mock_requests_get():
    response_data = {}

    def _set_json(data):
        response_data.clear()
        response_data.update(data)

    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = lambda: response_data
        mock_get.return_value = mock_response
        yield _set_json, mock_get


@pytest.fixture()
def load_answers():
    with patch("sunbeam.steps.sso.questions.load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch("sunbeam.steps.sso.questions.write_answers") as p:
        yield p


class BaseExternalProviderTest:
    step_class = None
    provider_name = "test-idp"
    charm_config = {
        "client-id": "test-client",
        "client-secret": "test-secret",
        "label": "Test Label",
    }
    fake_oidc_doc = {
        "issuer": "https://example.com",
        "authorization_endpoint": "https://example.com/auth",
        "token_endpoint": "https://example.com/token",
        "jwks_uri": "https://example.com/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    def setup_method(self):
        self.deployment = Mock()
        self.jhelper = Mock()

    def _get_step(self):
        return self.step_class(
            self.deployment,
            self.jhelper,
            "openid",
            self.provider_name,
            self.charm_config,
        )

    def test_is_skip(self):
        step = self._get_step()
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        step = self._get_step()
        assert step.has_prompts()

    def test_prompt_with_empty_answers(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        for k, v in list(step._questions.items()):
            step._questions[k].ask = Mock(return_value="")
        with pytest.raises(
            click.ClickException, match="client_id and client_secret are mandatory"
        ):
            step.prompt()
        # When all prompts are empty, we fail at the common questions, which are
        # the client_id and client_secret.
        step._questions["client_id"].ask.assert_called_once()
        step._questions["client_secret"].ask.assert_called_once()

    def test_run_success(
        self, read_config, update_config, mock_requests_get, load_answers, write_answers
    ):
        set_json, mock_get = mock_requests_get
        set_json(self.fake_oidc_doc)
        load_answers.return_value = {}

        step = self._get_step()
        step.prompt()
        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        mock_get.assert_called_once()


class TestGoogleProvider(BaseExternalProviderTest):
    step_class = AddGoogleProviderStep


class TestOktaProvider(BaseExternalProviderTest):
    step_class = AddOktaProviderStep
    charm_config = {
        "client-id": "test-client",
        "client-secret": "test-secret",
        "label": "Test Label",
        "okta_org": "test-org",
    }

    def test_missing_okta_org_asks_for_input(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["okta_org"].ask = Mock(return_value="dummy")
        step.prompt()
        step._questions["okta_org"].ask.assert_called_once()

    def test_missing_okta_org_will_err(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["okta_org"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="okta_org is mandatory"):
            step.prompt()
        step._questions["okta_org"].ask.assert_called_once()


class TestEntraProvider(BaseExternalProviderTest):
    step_class = AddEntraProviderStep
    charm_config = {
        "client-id": "test-client",
        "client-secret": "test-secret",
        "label": "Test Label",
        "microsoft_tenant": "tenant-123",
    }

    def test_missing_tenant_asks_for_input(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["microsoft_tenant"].ask = Mock(return_value="dummy")
        step.prompt()
        step._questions["microsoft_tenant"].ask.assert_called_once()

    def test_missing_tenant_will_err(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["microsoft_tenant"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="microsoft_tenant is mandatory"):
            step.prompt()
        step._questions["microsoft_tenant"].ask.assert_called_once()


class TestGenericProvider(BaseExternalProviderTest):
    step_class = AddGenericProviderStep
    charm_config = {
        "client-id": "test-client",
        "client-secret": "test-secret",
        "label": "Test Label",
        "issuer-url": "https://example.com",
    }

    def test_missing_issuer_url_asks_for_input(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["issuer_url"].ask = Mock(return_value="dummy")
        step.prompt()
        step._questions["issuer_url"].ask.assert_called_once()

    def test_missing_issuer_url_will_err(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["issuer_url"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="issuer_url is mandatory"):
            step.prompt()
        step._questions["issuer_url"].ask.assert_called_once()


class TestCanonicalProvider:
    def test_prompt_validation(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = AddCanonicalProviderStep(Mock(), Mock(), "openid", "canon-idp", {})
        step._questions["oauth_offer"].ask = Mock(return_value="")
        step._questions["cert_offer"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="oauth_offer is mandatory"):
            step.prompt()


class TestRemoveExternalProviderStep:
    def test_remove_provider(self, read_config, update_config):
        deployment = Mock()
        mock_client = Mock()
        mock_client.cluster.update_config = Mock()
        deployment.get_client.return_value = mock_client
        tfhelper = Mock()
        deployment.get_tfhelper.return_value = tfhelper

        config_store = {
            "TerraformVarsOpenstack": {"sso-providers": {"test-idp": {}}},
            "SSOFeatureConfigKey": {"test-idp": {}},
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = RemoveExternalProviderStep(
            deployment,
            Mock(),
            "test-idp",
        )
        step.tfhelper = tfhelper
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        update_config.assert_any_call(mock_client, "SSOFeatureConfigKey", {})
        update_config.assert_any_call(
            mock_client,
            "TerraformVarsOpenstack",
            {"sso-providers": {}},
        )


class TestUpdateExternalProviderStep:
    def test_update_provider(self, read_config, update_config):
        deployment = Mock()
        deployment.get_client.return_value = "client"
        deployment.get_tfhelper.return_value = Mock()
        provider_name = "test-idp"
        secrets = {"client_id": "id", "client_secret": "secret"}

        config_store = {
            "TerraformVarsOpenstack": {},
            "SSOFeatureConfigKey": {
                provider_name: {
                    "provider_type": "okta",
                    "config": {"client_id": "old", "client_secret": "old"},
                }
            },
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = UpdateExternalProviderStep(
            deployment,
            Mock(),
            provider_name,
            secrets,
        )
        step.tfhelper = Mock()
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        update_config.assert_any_call(
            "client",
            "SSOFeatureConfigKey",
            config_store["SSOFeatureConfigKey"],
        )
        update_config.assert_any_call(
            "client",
            "TerraformVarsOpenstack",
            config_store["TerraformVarsOpenstack"],
        )
        step.tfhelper.apply.assert_called_once()
