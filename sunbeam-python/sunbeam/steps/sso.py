# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import logging
import queue
from typing import Any

import click
import requests
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core import questions
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
)
from sunbeam.steps.openstack import CONFIG_KEY

LOG = logging.getLogger(__name__)
_GOOGLE_ISSUER_URL = "https://accounts.google.com"
_ENTRA_ISSUER_URL = "https://login.microsoftonline.com/%s/v2.0"
_CONFIG = "FeatureSSOExternalIDPConfig-%s"
SSO_CONFIG_KEY = "SSOFeatureConfigKey"
_OKTA_ISSUER_URL = "https://%s.okta.com"
APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
APPLICATION_REMOVE_TIMEOUT = 300  # 5 minutes
_BASE_QUESTIONS: dict[str, questions.Question] = {
    "client_id": questions.PromptQuestion("OAuth client-id"),
    "client_secret": questions.PasswordPromptQuestion(
        "OAuth client-secret",
        password=True,
    ),
    "label": questions.PromptQuestion("Label for this provider (optional)"),
}
_OKTA_QUESTIONS = _BASE_QUESTIONS | {
    "okta_org": questions.PromptQuestion("Your Okta org (eg: dev-123456)")
}

_ENTRA_QUESTIONS = _BASE_QUESTIONS | {
    "microsoft_tenant": questions.PromptQuestion("Microsoft tenant ID")
}
_GENERIC_PROVIDER_QUESTIONS = _BASE_QUESTIONS | {
    "issuer_url": questions.PromptQuestion(
        "OpenID Issuer URL",
        description=(
            "The issuer URL is a unique identifier for an "
            "OpenID provider. The URL must be https, it "
            "may have an optional path and is used when "
            "the provider type is set to generic."
        ),
    )
}

_CANONICAL_IAM_QUESTIONS: dict[str, questions.Question] = {
    "oauth_offer": questions.PromptQuestion(
        "OAuth juju offer",
        description=(
            "This is a juju offer created in another juju "
            "model. The offer must expose a relation which "
            "implements the 'oauth' interface. This is "
            "mandatory when the provider type is set to "
            "'canonical' and is typically used to relate "
            "to a hydra charm deployed by canonical identity "
            "platform, but other chrms may implement the "
            "same interface."
        ),
    ),
    "cert_offer": questions.PromptQuestion(
        "OAuth cert authority",
        description=(
            "When relating to a charm that implements the "
            "'oauth' interface, you may need to also relate "
            "to a certificate authority that implements the "
            "send-cert interface"
        ),
    ),
}

console = Console()


class _OIDCValidationMixin:
    def _validate_oidc_config(self, name: str, idp: dict) -> None:
        """Basic check for openid connect discovery document."""
        issuer_url = idp.get("config", {}).get("issuer_url", None)
        if not issuer_url:
            raise ValueError(
                f"could not find issuer_url for {name}",
            )

        issuer_url = issuer_url.rstrip("/")
        discovery_ep = f"{issuer_url}/.well-known/openid-configuration"
        cfg_req = requests.get(discovery_ep, timeout=10)
        cfg_req.raise_for_status()
        data = cfg_req.json()

        # see: https://openid.net/specs/openid-connect-discovery-1_0.html
        mandatory_openid_fields = [
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "response_types_supported",
            "subject_types_supported",
            "id_token_signing_alg_values_supported",
        ]
        missing = []
        for required in mandatory_openid_fields:
            if required not in data:
                missing.append(required)

        if missing:
            raise ValueError(
                (
                    f"Missing required fields in OIDC discovery document: "
                    f"{', '.join(missing)}"
                ),
            )


class RemoveExternalProviderStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        provider_name,
    ):
        super().__init__(
            "Remove external IDP",
            f"Removing external IDP {provider_name}",
        )
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.deployment = deployment
        self.tfhelper = deployment.get_tfhelper("openstack-plan")
        self._provider_name = provider_name

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        if self._provider_name in tfvars.get("sso-providers", {}):
            del tfvars["sso-providers"][self._provider_name]
            self.tfhelper.write_tfvars(tfvars)
            update_config(self.client, CONFIG_KEY, tfvars)

        if self._provider_name in cfg:
            del cfg[self._provider_name]
            update_config(self.client, SSO_CONFIG_KEY, cfg)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_gone(
                [f"keystone-idp-{self._provider_name}"],
                OPENSTACK_MODEL,
                timeout=APPLICATION_REMOVE_TIMEOUT,
            )
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                ["keystone"],
                timeout=APPLICATION_REMOVE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))

        # Clear answers on delete.
        questions.write_answers(
            self.client,
            _CONFIG % self._provider_name,
            {},
        )
        return Result(ResultType.COMPLETED)


class UpdateExternalProviderStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        provider_name,
        secrets: dict[str, str],
    ):
        super().__init__(
            "Update external IDP",
            f"Updating external IDP {provider_name}",
        )
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.deployment = deployment
        self.tfhelper = deployment.get_tfhelper("openstack-plan")
        self._provider_name = provider_name
        self._secrets = self._validate_secrets(secrets)

    def _validate_secrets(self, data: dict[str, str]):
        if not data:
            raise click.ClickException(
                "Invalid config supplied. Config must contain key/value pairs"
            )

        required_configs: dict[str, str | None] = {
            "client_id": None,
            "client_secret": None,
        }

        for key, _ in required_configs.items():
            val = data.get(
                key,
                data.get(
                    key.replace("_", "-"),
                    None,
                ),
            )
            if not val:
                raise click.ClickException(f"Missing {key} in secrets file")
            required_configs[key] = val
        return required_configs

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        if self._provider_name not in cfg:
            return Result(ResultType.FAILED, "Provider not found")

        provider_type = cfg[self._provider_name].get("provider_type", None)
        if not provider_type or provider_type == "canonical":
            return Result(
                ResultType.FAILED,
                (
                    f"Provider {self._provider_name} of type "
                    "{provider_type} cannot be updated"
                ),
            )

        if "config" not in cfg[self._provider_name]:
            return Result(
                ResultType.FAILED,
                f"Provider {self._provider_name} is in an invalid state",
            )

        cfg[self._provider_name]["config"]["client_id"] = self._secrets["client_id"]
        cfg[self._provider_name]["config"]["client_secret"] = self._secrets[
            "client_secret"
        ]
        update_config(self.client, SSO_CONFIG_KEY, cfg)

        if tfvars.get("sso-providers"):
            tfvars["sso-providers"][self._provider_name] = cfg[self._provider_name][
                "config"
            ]
        else:
            tfvars["sso-providers"] = {
                self._provider_name: cfg[self._provider_name]["config"]
            }
        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, CONFIG_KEY, tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        charm_name = "keystone-idp-{}".format(self._provider_name)
        apps = ["keystone", "horizon", charm_name]
        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, status)
        try:
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                apps,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=app_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class _BaseProviderStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        name: str,
        description: str,
        provider_type: str,
        deployment: Deployment,
        jhelper: JujuHelper,
        provider_protocol: str,
        provider_name: str,
        charm_config: dict[str, str],
    ):
        super().__init__(name, description)
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.deployment = deployment
        self.tfhelper = deployment.get_tfhelper("openstack-plan")

        self._provider_name = provider_name
        self._provider_type = provider_type
        self._provider_protocol = provider_protocol
        self._questions: dict[str, questions.Question] = {}
        self._preseed = self._compose_preseed_from_config(charm_config)

    def _get_preseed_map(self):
        raise NotImplementedError()

    def _compose_preseed_from_config(self, data: dict[str, str]):
        preseed = self._get_preseed_map()

        if not data:
            return preseed

        for key, val in preseed.items():
            preseed[key] = data.get(
                key,
                data.get(
                    key.replace("_", "-"),
                    None,
                ),
            )
        return preseed

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    @property
    def _charm_config(self):
        raise NotImplementedError()

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        raise NotImplementedError()

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Prompt the user for any data not in the config.

        Based on the provider type, prompt the user for any options
        that are not specified in the config.

        :param console: the console to prompt on
        :type console: rich.console.Console (Optional)
        """
        variables = questions.load_answers(
            self.client,
            _CONFIG % self._provider_name,
        )
        sso_bank = questions.QuestionBank(
            questions=self._questions,
            console=console,
            preseed=self._preseed,
            previous_answers=variables,
            show_hint=show_hint,
        )
        variables = self._ask(sso_bank, variables)
        questions.write_answers(self.client, _CONFIG % self._provider_name, variables)


class _BaseExternalProviderStep(_BaseProviderStep, _OIDCValidationMixin):
    def __init__(self, *args):
        super().__init__(*args)
        self._issuer_url = None
        self._client_id = None
        self._client_secret = None
        self._label = None
        self._questions = copy.deepcopy(_BASE_QUESTIONS)

    def _get_preseed_map(self):
        return {
            "client_id": None,
            "client_secret": None,
            "label": None,
        }

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        self._client_id = q_bank.client_id.ask()
        self._client_secret = q_bank.client_secret.ask()
        self._label = q_bank.label.ask()

        if not all([self._client_id, self._client_secret]):
            raise click.ClickException("client_id and client_secret are mandatory")

        if not self._label:
            label_name = self._provider_name.capitalize()
            self._label = f"Log in with {label_name}"

        variables["label"] = self._label
        variables["client_id"] = self._client_id
        variables["client_secret"] = self._client_secret
        return variables

    @property
    def _charm_config(self):
        if not all(
            [
                self._issuer_url,
                self._client_id,
                self._client_secret,
                self._label,
                self._provider_name,
            ]
        ):
            raise click.ClickException("invalid state for provider step")
        return {
            "provider": "generic",
            "provider_id": self._provider_name,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "label": self._label,
            "issuer_url": self._issuer_url,
        }

    def run(self, status: Status | None = None) -> Result:
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        idp = cfg.get(self._provider_name)
        if idp:
            cfg[self._provider_name]["config"] = self._charm_config
        else:
            cfg[self._provider_name] = {
                "config": self._charm_config,
                "provider_type": self._provider_type,
                "provider_proto": self._provider_protocol,
            }
        update_config(self.client, SSO_CONFIG_KEY, cfg)

        try:
            self._validate_oidc_config(self._provider_name, cfg[self._provider_name])
        except Exception as e:
            return Result(ResultType.FAILED, str(e))

        for provider, data in cfg.items():
            if data.get("provider_type", None) == "canonical":
                continue
            if tfvars.get("sso-providers"):
                tfvars["sso-providers"][provider] = data["config"]
            else:
                tfvars["sso-providers"] = {provider: data["config"]}

        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, CONFIG_KEY, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        charm_name = f"keystone-idp-{self._provider_name}"
        apps = ["keystone", "horizon", charm_name]
        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, status)
        try:
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                apps,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=app_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class AddGoogleProviderStep(_BaseExternalProviderStep):
    def __init__(self, *args, **kw):
        super().__init__(
            "Add google external IDP",
            "Adding google external IDP",
            "google",
            *args,
            **kw,
        )
        self._issuer_url = _GOOGLE_ISSUER_URL


class AddOktaProviderStep(_BaseExternalProviderStep):
    def __init__(self, *args, **kw):
        super().__init__(
            "Add okta external IDP",
            "Adding okta external IDP",
            "okta",
            *args,
            **kw,
        )
        self._questions = copy.deepcopy(_OKTA_QUESTIONS)

    def _get_preseed_map(self):
        preseed = super()._get_preseed_map()
        preseed["okta_org"] = None
        return preseed

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask(q_bank, variables)
        okta_org = q_bank.okta_org.ask()
        if not okta_org:
            raise click.ClickException("okta_org is mandatory")
        self._issuer_url = _OKTA_ISSUER_URL % okta_org
        variables["okta_org"] = okta_org
        return variables


class AddEntraProviderStep(_BaseExternalProviderStep):
    def __init__(self, *args, **kw):
        super().__init__(
            "Add entra external IDP",
            "Adding entra external IDP",
            "entra",
            *args,
            **kw,
        )
        self._questions = copy.deepcopy(_ENTRA_QUESTIONS)

    def _get_preseed_map(self):
        preseed = super()._get_preseed_map()
        preseed["microsoft_tenant"] = None
        return preseed

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask(q_bank, variables)
        tenant_id = q_bank.microsoft_tenant.ask()
        if not tenant_id:
            raise click.ClickException("microsoft_tenant is mandatory")
        self._issuer_url = _ENTRA_ISSUER_URL % tenant_id
        variables["microsoft_tenant"] = tenant_id
        return variables


class AddGenericProviderStep(_BaseExternalProviderStep):
    def __init__(self, *args, **kw):
        super().__init__(
            "Add generic external IDP",
            "Adding generic external IDP",
            "generic",
            *args,
            **kw,
        )
        self._questions = copy.deepcopy(_GENERIC_PROVIDER_QUESTIONS)

    def _get_preseed_map(self):
        preseed = super()._get_preseed_map()
        preseed["issuer_url"] = None
        return preseed

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask(q_bank, variables)
        issuer_url = q_bank.issuer_url.ask()
        if not issuer_url:
            raise click.ClickException("issuer_url is mandatory")
        self._issuer_url = issuer_url
        variables["issuer_url"] = issuer_url
        return variables


class AddCanonicalProviderStep(_BaseProviderStep):
    def __init__(self, *args, **kw):
        super().__init__(
            "Add canonical IDP",
            "Adding canonical IDP",
            "canonical",
            *args,
            **kw,
        )
        self._oauth_offer = None
        self._cert_offer = None
        self._questions = copy.deepcopy(_CANONICAL_IAM_QUESTIONS)

    def _get_preseed_map(self):
        return {
            "oauth_offer": None,
            "cert_offer": None,
        }

    @property
    def _charm_config(self):
        if not self._oauth_offer:
            raise click.ClickException("Missing oauth offer")
        return {
            "oauth_offer": self._oauth_offer,
            "cert_offer": self._cert_offer,
        }

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        self._oauth_offer = q_bank.oauth_offer.ask()
        self._cert_offer = q_bank.cert_offer.ask()

        if not self._oauth_offer:
            raise click.ClickException("oauth_offer is mandatory")

        variables["oauth_offer"] = self._oauth_offer
        variables["cert_offer"] = self._cert_offer
        return variables

    def run(self, status: Status | None = None) -> Result:
        """Run configure steps."""
        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        idp = cfg.get(self._provider_name)
        if idp:
            cfg[self._provider_name]["config"] = self._charm_config
        else:
            cfg[self._provider_name] = {
                "config": self._charm_config,
                "provider_type": self._provider_type,
                "provider_proto": self._provider_protocol,
            }
        update_config(self.client, SSO_CONFIG_KEY, cfg)

        oauth_offer = cfg[self._provider_name]["config"]["oauth_offer"]
        try:
            self.jhelper.consume_offer(
                OPENSTACK_MODEL,
                oauth_offer,
                self._provider_name,
            )
            self.integrate(
                OPENSTACK_MODEL,
                f"{self._provider_name}",
                "keystone:oauth",
            )
        except Exception as e:
            return Result(ResultType.FAILED, str(e))

        cert_offer = cfg[self._provider_name]["config"].get("cert_offer")
        cert_saas_name = f"{self._provider_name}-cert"
        if cert_offer:
            try:
                self.jhelper.consume_offer(
                    OPENSTACK_MODEL,
                    cert_offer,
                    cert_saas_name,
                )
                self.integrate(
                    OPENSTACK_MODEL,
                    f"{cert_saas_name}",
                    "keystone:receive-ca-cert",
                )
            except Exception as e:
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ValidateIdentityManifest(BaseStep, _OIDCValidationMixin):
    _provider_question_map = {
        "google": _BASE_QUESTIONS,
        "okta": _OKTA_QUESTIONS,
        "entra": _ENTRA_QUESTIONS,
        "generic": _GENERIC_PROVIDER_QUESTIONS,
    }

    def __init__(
        self,
        client: Client,
        manifest: Manifest | None = None,
    ):
        super().__init__("Identity manifest", "Validating identity manifest")
        self.client = client
        self.manifest = manifest
        self.variables: dict = {}

    def _issuer_url(self, provider: str, config: dict[str, str | None]):
        if provider == "google":
            return _GOOGLE_ISSUER_URL
        if provider == "okta":
            return _OKTA_ISSUER_URL % config["okta_org"]
        if provider == "entra":
            return _ENTRA_ISSUER_URL % config["microsoft_tenant"]
        if provider == "generic":
            return config["issuer_url"]
        raise click.ClickException(
            f"Cannot determine issuer_url for provider type {provider}"
        )

    def _charm_config(
        self,
        name: str,
        provider: str,
        protocol: str,
        config: dict[str, str | None],
    ):
        if not config.get("label"):
            config["label"] = f"Log in with {name}"
        return {
            "provider": "generic",
            "provider_id": name,
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "label": config["label"],
            "issuer_url": self._issuer_url(provider, config),
        }

    def _canonical_charm_config(
        self, name: str, provider: str, protocol: str, config: dict[str, str]
    ):
        oauth_offer = config.get(
            "oauth_offer",
            config.get("oauth-offer", None),
        )
        cert_offer = config.get(
            "cert_offer",
            config.get("cert-offer", None),
        )
        if not oauth_offer:
            raise click.ClickException(f"Missing oauth_offer for {name}")
        return {
            "oauth_offer": oauth_offer,
            "cert_offer": cert_offer,
        }

    def _external_charm_config(
        self, name: str, provider: str, protocol: str, config: dict[str, str]
    ) -> dict[str, Any]:
        questions = self._provider_question_map.get(provider, None)
        if not questions:
            raise click.ClickException(f"Unknown provider type {provider} for {name}")

        missing_keys = []
        norm_config = {}
        for key in questions:
            norm_key = key.replace("-", "_")
            cfg_val = config.get(
                norm_key,
                config.get(
                    norm_key.replace("_", "-"),
                    None,
                ),
            )
            if not cfg_val:
                missing_keys.append(key)
            norm_config[norm_key] = cfg_val

        if missing_keys:
            raise click.ClickException(
                f"Missing config for provider {name} ({provider}): "
                f"{', '.join(missing_keys)}"
            )

        parsed_conf = self._charm_config(name, provider, protocol, norm_config)
        self._validate_oidc_config(name, {"config": parsed_conf})
        return parsed_conf

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.manifest or not self.manifest.core.config.identity:
            return Result(ResultType.SKIPPED)

        if not self.manifest.core.config.identity.profiles:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        if not self.manifest:
            return Result(ResultType.COMPLETED)

        if not self.manifest.core.config.identity:
            return Result(ResultType.COMPLETED)

        profiles = self.manifest.core.config.identity.profiles
        if not profiles:
            return Result(ResultType.COMPLETED)

        try:
            for name, config in profiles.items():
                cfg[name] = {
                    "provider_type": config.provider,
                    "provider_proto": config.protocol,
                }
                if config.provider == "canonical":
                    cfg[name]["config"] = self._canonical_charm_config(
                        name,
                        config.provider,
                        config.protocol,
                        config.config,
                    )
                else:
                    cfg[name]["config"] = self._external_charm_config(
                        name,
                        config.provider,
                        config.protocol,
                        config.config,
                    )
            update_config(self.client, SSO_CONFIG_KEY, cfg)
        except Exception as err:
            return Result(ResultType.FAILED, str(err))

        return Result(ResultType.COMPLETED)


class DeployIdentityProvidersStep(BaseStep, JujuStepHelper):
    """Deploy identity providers on bootstrap."""

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
    ):
        super().__init__("Identity providers", "Deploying identity providers")
        self.client = deployment.get_client()
        self.manifest = manifest
        self.tfhelper = tfhelper
        self.jhelper = jhelper

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.manifest or not self.manifest.core.config.identity:
            return Result(ResultType.SKIPPED)

        if not self.manifest.core.config.identity.profiles:
            return Result(ResultType.SKIPPED)

        try:
            state = self.tfhelper.pull_state()
            self._has_tf_resources = bool(state.get("resources"))
        except TerraformException:
            LOG.debug("Failed to pull state", exc_info=True)

        self._has_juju_resources = self.jhelper.model_exists(OPENSTACK_MODEL)

        if not self._has_tf_resources and not self._has_juju_resources:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        try:
            cfg = read_config(self.client, SSO_CONFIG_KEY)
        except ConfigItemNotFoundException:
            cfg = {}

        apps = ["keystone", "horizon"]
        canonical_providers = {}
        for provider, data in cfg.items():
            if data.get("provider_type", None) == "canonical":
                canonical_providers[provider] = data
                continue
            if tfvars.get("sso-providers"):
                tfvars["sso-providers"][provider] = data["config"]
            else:
                tfvars["sso-providers"] = {provider: data["config"]}
            apps.append(f"keystone-idp-{provider}")

        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, CONFIG_KEY, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, status)
        try:
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                apps,
                timeout=APPLICATION_DEPLOY_TIMEOUT,
                queue=app_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        for provider, data in canonical_providers.items():
            oauth_offer = data["config"]["oauth_offer"]
            try:
                self.jhelper.consume_offer(
                    OPENSTACK_MODEL,
                    oauth_offer,
                    provider,
                )
                self.integrate(
                    OPENSTACK_MODEL,
                    f"{provider}",
                    "keystone:oauth",
                )
            except Exception as e:
                return Result(ResultType.FAILED, str(e))

            cert_offer = data["config"].get("cert_offer")
            cert_saas_name = f"{provider}-cert"
            if cert_offer:
                try:
                    self.jhelper.consume_offer(
                        OPENSTACK_MODEL,
                        cert_offer,
                        cert_saas_name,
                    )
                    self.integrate(
                        OPENSTACK_MODEL,
                        f"{cert_saas_name}",
                        "keystone:receive-ca-cert",
                    )
                except Exception as e:
                    return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
