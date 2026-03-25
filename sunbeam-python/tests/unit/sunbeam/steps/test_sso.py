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
    SetKeystoneSAMLCertAndKeyStep,
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

    def test_is_skip(self, step_context):
        step = self._get_step()
        result = step.is_skip(step_context)
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
        self,
        read_config,
        update_config,
        mock_requests_get,
        load_answers,
        write_answers,
        step_context,
    ):
        set_json, mock_get = mock_requests_get
        set_json(self.fake_oidc_doc)
        load_answers.return_value = {}

        step = self._get_step()
        step.prompt()
        result = step.run(step_context)

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


class BaseSAML2ProviderTest:
    step_class = None
    provider_name = "test-saml2-idp"
    charm_config = {
        "app-id": "test-app-123",
        "label": "Test SAML2 Label",
    }
    fake_saml2_metadata = """<?xml version="1.0" encoding="UTF-8"?>
<EntityDescriptor entityID="https://example.com/saml2/idp" xmlns="urn:oasis:names:tc:SAML:2.0:metadata">
    <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    </IDPSSODescriptor>
</EntityDescriptor>"""

    def setup_method(self):
        self.deployment = Mock()
        self.jhelper = Mock()

    def _get_step(self):
        return self.step_class(
            self.deployment,
            self.jhelper,
            "saml2",
            self.provider_name,
            self.charm_config,
        )

    def test_is_skip(self, step_context):
        step = self._get_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        step = self._get_step()
        assert step.has_prompts()

    def test_prompt_with_empty_answers(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        for k, v in list(step._questions.items()):
            step._questions[k].ask = Mock(return_value="")

        # For generic SAML2 provider, metadata_url is the mandatory field, not app_id
        if isinstance(step, AddGenericProviderStep):
            with pytest.raises(click.ClickException, match="metadata-url is mandatory"):
                step.prompt()
            step._questions["metadata_url"].ask.assert_called_once()
        else:
            with pytest.raises(click.ClickException, match="app_id is mandatory"):
                step.prompt()
            step._questions["app_id"].ask.assert_called_once()

    def test_run_success(
        self, read_config, update_config, load_answers, write_answers, step_context
    ):
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_response.text = self.fake_saml2_metadata
            mock_get.return_value = mock_response

            load_answers.return_value = {}

            step = self._get_step()
            step.prompt()
            result = step.run(step_context)

            assert result.result_type == ResultType.COMPLETED
            mock_get.assert_called_once()


class TestGoogleSAML2Provider(BaseSAML2ProviderTest):
    step_class = AddGoogleProviderStep


class TestOktaSAML2Provider(BaseSAML2ProviderTest):
    step_class = AddOktaProviderStep
    charm_config = {
        "app-id": "test-app-123",
        "label": "Test SAML2 Label",
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


class TestEntraSAML2Provider(BaseSAML2ProviderTest):
    step_class = AddEntraProviderStep
    charm_config = {
        "app-id": "test-app-123",
        "label": "Test SAML2 Label",
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


class TestGenericSAML2Provider(BaseSAML2ProviderTest):
    step_class = AddGenericProviderStep
    charm_config = {
        "label": "Test SAML2 Label",
        "metadata_url": "https://example.com/saml2/metadata.xml",
        "ca_chain": "",
    }

    def test_missing_metadata_url_asks_for_input(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["metadata_url"].ask = Mock(return_value="dummy")
        step.prompt()
        step._questions["metadata_url"].ask.assert_called_once()

    def test_missing_metadata_url_will_err(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        step._questions["metadata_url"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="metadata-url is mandatory"):
            step.prompt()
        step._questions["metadata_url"].ask.assert_called_once()

    def test_prompt_with_empty_answers(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = self._get_step()

        for k, v in list(step._questions.items()):
            step._questions[k].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="metadata-url is mandatory"):
            step.prompt()
        step._questions["metadata_url"].ask.assert_called_once()

    def test_run_success(
        self, read_config, update_config, load_answers, write_answers, step_context
    ):
        # Mock the SAML2 validation to avoid the bytes/string issue in tempfile
        with (
            patch("requests.get") as mock_get,
            patch("sunbeam.steps.sso._validate_saml2_config") as mock_validate,
        ):
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_response.text = self.fake_saml2_metadata
            mock_get.return_value = mock_response
            mock_validate.return_value = None

            load_answers.return_value = {}

            step = self._get_step()
            # Override the _ask_saml2 method questions
            step._questions["metadata_url"].ask = Mock(
                return_value="https://example.com/saml2/metadata.xml"
            )
            step._questions["ca_chain"].ask = Mock(
                return_value="dummy-ca-content"
            )  # Non-empty to pass all(cfg.values()) check
            step._questions["label"].ask = Mock(return_value="Test Label")
            step.prompt()
            result = step.run(step_context)

            assert result.result_type == ResultType.COMPLETED


class TestCanonicalProvider:
    def test_prompt_validation(self, load_answers, write_answers):
        load_answers.return_value = {}
        step = AddCanonicalProviderStep(Mock(), Mock(), "openid", "canon-idp", {})
        step._questions["oauth_offer"].ask = Mock(return_value="")
        step._questions["cert_offer"].ask = Mock(return_value="")
        with pytest.raises(click.ClickException, match="oauth_offer is mandatory"):
            step.prompt()


class TestRemoveExternalProviderStep:
    def test_remove_provider(self, read_config, update_config, step_context):
        deployment = Mock()
        mock_client = Mock()
        mock_client.cluster.update_config = Mock()
        deployment.get_client.return_value = mock_client
        tfhelper = Mock()
        deployment.get_tfhelper.return_value = tfhelper

        config_store = {
            "TerraformVarsOpenstack": {
                "sso-providers": {"openid": {"test-idp": {}}, "saml2": {}}
            },
            "SSOFeatureConfigKey": {
                "openid": {"test-idp": {"provider_type": "google"}},
                "saml2": {},
            },
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = RemoveExternalProviderStep(
            deployment,
            Mock(),
            "test-idp",
            "openid",
        )
        step.tfhelper = tfhelper
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        update_config.assert_any_call(
            mock_client, "SSOFeatureConfigKey", {"openid": {}, "saml2": {}}
        )
        update_config.assert_any_call(
            mock_client,
            "TerraformVarsOpenstack",
            {"sso-providers": {"openid": {}, "saml2": {}}},
        )


class TestUpdateExternalProviderStep:
    def test_update_provider(self, read_config, update_config, step_context):
        deployment = Mock()
        deployment.get_client.return_value = "client"
        deployment.get_tfhelper.return_value = Mock()
        provider_name = "test-idp"
        secrets = {"client_id": "id", "client_secret": "secret"}

        config_store = {
            "TerraformVarsOpenstack": {"sso-providers": {"openid": {}, "saml2": {}}},
            "SSOFeatureConfigKey": {
                "openid": {
                    provider_name: {
                        "provider_type": "okta",
                        "config": {"client_id": "old", "client_secret": "old"},
                    }
                },
                "saml2": {},
            },
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = UpdateExternalProviderStep(
            deployment,
            Mock(),
            provider_name,
            "openid",
            secrets,
        )
        step.tfhelper = Mock()
        result = step.run(step_context)
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

    def test_update_saml2_provider_has_no_secrets(
        self, read_config, update_config, step_context
    ):
        deployment = Mock()
        deployment.get_client.return_value = "client"
        deployment.get_tfhelper.return_value = Mock()
        provider_name = "test-saml2-idp"
        secrets = {}  # SAML2 has no secrets

        config_store = {
            "TerraformVarsOpenstack": {"sso-providers": {"openid": {}, "saml2": {}}},
            "SSOFeatureConfigKey": {
                "openid": {},
                "saml2": {
                    provider_name: {
                        "provider_type": "google",
                        "config": {
                            "metadata-url": "https://example.com/saml",
                            "name": provider_name,
                        },
                    }
                },
            },
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = UpdateExternalProviderStep(
            deployment,
            Mock(),
            provider_name,
            "saml2",
            secrets,
        )
        step.tfhelper = Mock()
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_remove_saml2_provider(self, read_config, update_config, step_context):
        deployment = Mock()
        mock_client = Mock()
        mock_client.cluster.update_config = Mock()
        deployment.get_client.return_value = mock_client
        tfhelper = Mock()
        deployment.get_tfhelper.return_value = tfhelper

        config_store = {
            "TerraformVarsOpenstack": {
                "sso-providers": {"openid": {}, "saml2": {"test-saml2-idp": {}}}
            },
            "SSOFeatureConfigKey": {
                "openid": {},
                "saml2": {"test-saml2-idp": {"provider_type": "google"}},
            },
        }

        read_config.side_effect = lambda client, key: config_store[key]

        step = RemoveExternalProviderStep(
            deployment,
            Mock(),
            "test-saml2-idp",
            "saml2",
        )
        step.tfhelper = tfhelper
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        update_config.assert_any_call(
            mock_client, "SSOFeatureConfigKey", {"openid": {}, "saml2": {}}
        )
        update_config.assert_any_call(
            mock_client,
            "TerraformVarsOpenstack",
            {"sso-providers": {"openid": {}, "saml2": {}}},
        )


class TestSetKeystoneSAMLCertAndKeyStep:
    def setup_method(self):
        self.deployment = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()

    @pytest.fixture
    def mock_open_files(self):
        cert_content = (
            "-----BEGIN CERTIFICATE-----\nMOCKCERT\n-----END CERTIFICATE-----"
        )
        key_content = "-----BEGIN PRIVATE KEY-----\nMOCKKEY\n-----END PRIVATE KEY-----"

        def mock_open(filename, mode="r"):
            mock_file = Mock()
            if "cert" in filename:
                mock_file.read.return_value = cert_content
            else:  # key file
                mock_file.read.return_value = key_content
            return mock_file

        with patch("builtins.open", mock_open):
            yield cert_content, key_content

    def test_skip_when_no_manifest_or_files(self, step_context):
        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment, self.tfhelper, self.jhelper
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_skip_when_manifest_has_no_saml2_x509(self, step_context):
        self.manifest.core.config.identity = None
        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_has_no_prompts(self):
        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            x509_cert="/path/to/cert",
            x509_key="/path/to/key",
        )
        assert not step.has_prompts()

    @patch("sunbeam.steps.sso.cert_and_key_match")
    def test_run_with_files_success(
        self,
        mock_cert_match,
        read_config,
        update_config,
        mock_open_files,
        step_context,
    ):
        cert_content, key_content = mock_open_files
        mock_cert_match.return_value = True

        mock_client = Mock()
        self.deployment.get_client.return_value = mock_client

        # Mock juju helper methods
        self.jhelper.add_secret.return_value = "secret-id-123"
        self.jhelper.get_secret.return_value = {
            "certificate": cert_content,
            "key": key_content,
        }

        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            x509_cert="/path/to/cert.pem",
            x509_key="/path/to/key.pem",
        )

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

        # Verify certificate validation was called
        mock_cert_match.assert_called_once_with(
            cert_content.encode(), key_content.encode()
        )

        # Verify secret operations
        self.jhelper.add_secret.assert_called_once()
        self.jhelper.grant_secret.assert_called_once()

        # Verify terraform config update
        update_config.assert_called()
        self.tfhelper.apply.assert_called_once()

    @patch("sunbeam.steps.sso.cert_and_key_match")
    def test_run_cert_key_mismatch_fails(
        self, mock_cert_match, mock_open_files, step_context
    ):
        mock_cert_match.return_value = False

        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            x509_cert="/path/to/cert.pem",
            x509_key="/path/to/key.pem",
        )

        with pytest.raises(ValueError, match="Certificate .* is not derived from"):
            step.run(step_context)

    def test_run_file_read_error_fails(self, step_context):
        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            x509_cert="/nonexistent/cert.pem",
            x509_key="/nonexistent/key.pem",
        )

        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.steps.sso.cert_and_key_match")
    def test_run_updates_existing_secret(
        self,
        mock_cert_match,
        read_config,
        update_config,
        mock_open_files,
        step_context,
    ):
        cert_content, key_content = mock_open_files
        mock_cert_match.return_value = True

        mock_client = Mock()
        self.deployment.get_client.return_value = mock_client

        # Setup existing secret
        existing_secret_id = "existing-secret-123"
        config_store = {
            "KeystoneSAML2ConfigKey": {"saml2_cert_key_secret": existing_secret_id}
        }
        read_config.side_effect = lambda client, key: config_store.get(key, {})

        # Mock existing secret with different content
        old_cert = "-----BEGIN CERTIFICATE-----\nOLDCERT\n-----END CERTIFICATE-----"
        self.jhelper.get_secret.return_value = {
            "certificate": old_cert,
            "key": key_content,
        }

        step = SetKeystoneSAMLCertAndKeyStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            x509_cert="/path/to/cert.pem",
            x509_key="/path/to/key.pem",
        )

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

        # Should update the existing secret, not create a new one
        self.jhelper.add_secret.assert_not_called()
        self.jhelper.update_secret.assert_called_once()

        # Verify the secret update contains new certificate
        update_call = self.jhelper.update_secret.call_args
        assert update_call[1]["data"]["certificate"] == cert_content

    def test_run_with_manifest_success(self, read_config, update_config, step_context):
        # Setup manifest with SAML2 x509 config
        self.manifest.core.config.identity.saml2_x509.certificate = "/path/to/cert.pem"
        self.manifest.core.config.identity.saml2_x509.key = "/path/to/key.pem"

        cert_content = (
            "-----BEGIN CERTIFICATE-----\nMANIFESTCERT\n-----END CERTIFICATE-----"
        )
        key_content = (
            "-----BEGIN PRIVATE KEY-----\nMANIFESTKEY\n-----END PRIVATE KEY-----"
        )

        def mock_open(filename, mode="r"):
            mock_file = Mock()
            if "cert" in filename:
                mock_file.read.return_value = cert_content
            else:
                mock_file.read.return_value = key_content
            return mock_file

        with (
            patch("builtins.open", mock_open),
            patch("sunbeam.steps.sso.cert_and_key_match", return_value=True),
        ):
            mock_client = Mock()
            self.deployment.get_client.return_value = mock_client
            self.jhelper.add_secret.return_value = "manifest-secret-456"
            self.jhelper.get_secret.return_value = {
                "certificate": cert_content,
                "key": key_content,
            }

            step = SetKeystoneSAMLCertAndKeyStep(
                self.deployment, self.tfhelper, self.jhelper, self.manifest
            )

            result = step.run(step_context)
            assert result.result_type == ResultType.COMPLETED
