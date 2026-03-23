# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import logging
import queue
import tempfile
import xml.etree.ElementTree as ET  # noqa: S405
from typing import (
    Any,
    Mapping,
)

import click
import requests
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core import questions
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
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
from sunbeam.features.interface.utils import cert_and_key_match
from sunbeam.steps.openstack import CONFIG_KEY

LOG = logging.getLogger(__name__)
SSO_CONFIG_KEY = "SSOFeatureConfigKey"
_CONFIG = "FeatureSSOExternalIDPConfig-%(proto)s-%(name)s"
_SAML2_CERT_KEY_SECRET = "keystone-saml2-x509-key-cert"
_SAML2_CONFIG_KEY = "KeystoneSAML2ConfigKey"

_GOOGLE_ISSUER_URL = "https://accounts.google.com"
_ENTRA_ISSUER_URL = "https://login.microsoftonline.com/%(microsoft_tenant)s/v2.0"
_OKTA_ISSUER_URL = "https://%(okta_org)s.okta.com"

_GOOGLE_SAML2_METADATA_URL = "https://accounts.google.com/o/saml2/idp?idpid=%(app_id)s"
_ENTRA_SAML_METADATA_URL = (
    "https://login.microsoftonline.com/%(microsoft_tenant)s/federationmetadata/2007-06"
    "/federationmetadata.xml?appid=%(app_id)s"
)
_OKTA_SAML2_METADATA_URL = (
    "https://%(okta_org)s.okta.com/app/%(app_id)s/sso/saml/metadata"
)

APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
APPLICATION_REMOVE_TIMEOUT = 300  # 5 minutes
_BASE_QUESTIONS_OPENID: dict[str, questions.Question] = {
    "client_id": questions.PromptQuestion("OAuth client-id"),
    "client_secret": questions.PasswordPromptQuestion(
        "OAuth client-secret",
        password=True,
    ),
    "label": questions.PromptQuestion("Label for this provider (optional)"),
}
_OKTA_QUESTIONS_OPENID = _BASE_QUESTIONS_OPENID | {
    "okta_org": questions.PromptQuestion("Your Okta org (eg: dev-123456)")
}

_ENTRA_QUESTIONS_OPENID = _BASE_QUESTIONS_OPENID | {
    "microsoft_tenant": questions.PromptQuestion("Microsoft tenant ID")
}
_GENERIC_PROVIDER_QUESTIONS_OPENID = _BASE_QUESTIONS_OPENID | {
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

_BASE_QUESTIONS_SAML2: dict[str, questions.Question] = {
    "app_id": questions.PromptQuestion(
        "SAML2 application ID",
        description=(
            "The SAML2 application ID you want to enable for this"
            "provider. You should be able to find it in the dashboard"
            "of your IDP."
        ),
    ),
    "label": questions.PromptQuestion("Label for this provider (optional)"),
}
_ENTRA_QUESTIONS_SAML2 = _BASE_QUESTIONS_SAML2 | {
    "microsoft_tenant": questions.PromptQuestion("Microsoft tenant ID")
}
_OKTA_QUESTIONS_SAML2 = _BASE_QUESTIONS_SAML2 | {
    "okta_org": questions.PromptQuestion("Your Okta org (eg: dev-123456)")
}
_GENERIC_PROVIDER_QUESTIONS_SAML2: dict[str, questions.Question] = {
    "metadata_url": questions.PromptQuestion("SAML2 metadata URL"),
    "ca_chain": questions.PromptQuestion(
        "CA certificate chain",
        description=(
            "The CA certificate chain used to validate this generic SAML2"
            "provider. This needs to be the path to the PEM encoded CA chain."
        ),
    ),
    "label": questions.PromptQuestion("Label for this provider (optional)"),
}

_QUESTIONS = {
    "openid": {
        "base": _BASE_QUESTIONS_OPENID,
        "google": _BASE_QUESTIONS_OPENID,
        "entra": _ENTRA_QUESTIONS_OPENID,
        "okta": _OKTA_QUESTIONS_OPENID,
        "generic": _GENERIC_PROVIDER_QUESTIONS_OPENID,
    },
    "saml2": {
        "base": _BASE_QUESTIONS_SAML2,
        "google": _BASE_QUESTIONS_SAML2,
        "entra": _ENTRA_QUESTIONS_SAML2,
        "okta": _OKTA_QUESTIONS_SAML2,
        "generic": _GENERIC_PROVIDER_QUESTIONS_SAML2,
    },
}

_METADATA_URL_MAP = {
    "openid": {
        "google": _GOOGLE_ISSUER_URL,
        "entra": _ENTRA_ISSUER_URL,
        "okta": _OKTA_ISSUER_URL,
    },
    "saml2": {
        "google": _GOOGLE_SAML2_METADATA_URL,
        "entra": _ENTRA_SAML_METADATA_URL,
        "okta": _OKTA_SAML2_METADATA_URL,
    },
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
VALID_SSO_PROTOCOLS = [
    "openid",
    "saml2",
]

console = Console()


def safe_get_sso_config(client: Client):
    """Read SSO config with a fallback to empty protocol values."""
    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {
            "openid": {},
            "saml2": {},
        }

    if not cfg.get("openid"):
        cfg["openid"] = {}
    if not cfg.get("saml2"):
        cfg["saml2"] = {}
    return cfg


def _safe_get_tfvars(client: Client):
    try:
        tfvars = read_config(client, CONFIG_KEY)
    except ConfigItemNotFoundException:
        tfvars = {
            "sso-providers": {
                "openid": {},
                "saml2": {},
            },
        }
    if not tfvars.get("sso-providers"):
        tfvars["sso-providers"] = {
            "openid": {},
            "saml2": {},
        }
    else:
        if not tfvars["sso-providers"].get("openid"):
            tfvars["sso-providers"]["openid"] = {}
        if not tfvars["sso-providers"].get("saml2"):
            tfvars["sso-providers"]["saml2"] = {}
    return tfvars


def _validate_oidc_config(name: str, idp: dict) -> None:
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


def _validate_saml2_config(name: str, idp: dict) -> str:
    """Basic check for saml2 metadata URL."""
    config = idp.get("config", {})
    if not config:
        raise ValueError(
            "invalid config for IDP",
        )
    metadata_url = config.get("metadata-url", None)
    if not metadata_url:
        raise ValueError(
            "could not find metadata-url",
        )

    chain = config.get("ca-chain", "")
    with tempfile.NamedTemporaryFile() as fd:
        verify: bool | str = True
        if chain:
            fd.write(chain)
            fd.flush()
            verify = fd.name
        cfg_req = requests.get(metadata_url, verify=verify, timeout=10)
        cfg_req.raise_for_status()

    # return the response and remove any potential UTF-8 byte order mark.
    data = cfg_req.text.lstrip("\ufeff")
    root = ET.fromstring(data)  # noqa: S314
    entity_id = root.attrib.get("entityID", "")
    if not entity_id:
        raise ValueError(
            f"failed to determine entityID for idp {name}",
        )
    return entity_id


def _validate_idp(protocol: str, name: str, idp: dict) -> None | str:
    """Basic check for idp configuration options."""
    validator_map = {
        "openid": _validate_oidc_config,
        "saml2": _validate_saml2_config,
    }
    validate_fn = validator_map.get(protocol, None)
    if not validate_fn:
        raise click.ClickException(f"Cannot validate protocol {protocol}")
    result = validate_fn(name, idp)
    return result


class RemoveExternalProviderStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        provider_name: str,
        provider_proto: str,
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
        if provider_proto not in VALID_SSO_PROTOCOLS:
            raise ValueError(f"Invalid protocol {provider_proto}")
        self._proto = provider_proto

    def run(self, context: StepContext) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        tfvars = _safe_get_tfvars(self.client)
        cfg = safe_get_sso_config(self.client)

        if self._provider_name in tfvars["sso-providers"][self._proto]:
            del tfvars["sso-providers"][self._proto][self._provider_name]
            self.tfhelper.write_tfvars(tfvars)
            update_config(self.client, CONFIG_KEY, tfvars)

        cfg_provider = cfg[self._proto].get(self._provider_name, {})
        if not cfg_provider:
            return Result(ResultType.COMPLETED)

        cfg[self._proto].pop(self._provider_name, None)
        update_config(self.client, SSO_CONFIG_KEY, cfg)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_gone(
                [f"keystone-idp-{self._proto}-{self._provider_name}"],
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
            _CONFIG % {"name": self._provider_name, "proto": self._proto},
            {},
        )
        return Result(ResultType.COMPLETED)


class UpdateExternalProviderStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
        provider_name: str,
        provider_proto: str,
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
        self._proto = provider_proto
        if provider_proto not in VALID_SSO_PROTOCOLS:
            raise ValueError(f"Invalid protocol {provider_proto}")
        self._secrets = self._validate_secrets(secrets)

    def _update_openid(self, cfg, tfvars):
        cfg[self._proto][self._provider_name]["config"]["client_id"] = self._secrets[
            "client_id"
        ]
        cfg[self._proto][self._provider_name]["config"]["client_secret"] = (
            self._secrets["client_secret"]
        )
        update_config(self.client, SSO_CONFIG_KEY, cfg)

        tfvars["sso-providers"][self._proto][self._provider_name] = cfg[self._proto][
            self._provider_name
        ]["config"]

        return (cfg, tfvars)

    def _update_saml2(self, cfg, tfvars):
        # No secrets to update for SAML2.
        return (cfg, tfvars)

    def _validate_secrets_openid(self, data: dict[str, str]):
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

    def _validate_secrets_saml2(self, data: dict[str, str]):
        # SAML2 has no secrets.
        return {}

    def _validate_secrets(self, data: dict[str, str]):
        validate_fn = getattr(self, f"_validate_secrets_{self._proto}", None)
        if not validate_fn:
            raise click.ClickException(
                f"No validation can be done for protocol {self._proto}"
            )
        return validate_fn(data)

    def run(self, context: StepContext) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        tfvars = _safe_get_tfvars(self.client)
        cfg = safe_get_sso_config(self.client)

        if not cfg[self._proto].get(self._provider_name, None):
            return Result(
                ResultType.FAILED,
                f"Provider {self._provider_name} ({self._proto}) not found",
            )

        provider_type = cfg[self._proto][self._provider_name].get(
            "provider_type",
            None,
        )
        if not provider_type or provider_type == "canonical":
            return Result(
                ResultType.FAILED,
                (f"Provider {self._provider_name} ({self._proto})cannot be updated"),
            )

        if "config" not in cfg[self._proto][self._provider_name]:
            return Result(
                ResultType.FAILED,
                f"Provider {self._provider_name} ({self._proto})"
                " is in an invalid state",
            )

        update_fn = getattr(self, f"_update_{self._proto}", None)
        if not update_fn:
            return Result(
                ResultType.FAILED, f"No update possible for protocol {self._proto}"
            )
        cfg, tfvars = update_fn(cfg, tfvars)

        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, CONFIG_KEY, tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        charm_name = f"keystone-idp-{self._proto}-{self._provider_name}"
        apps = ["keystone", "horizon", charm_name]
        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, context.status)
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
        self._proto = provider_protocol
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

    def is_skip(self, context: StepContext) -> Result:
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
        asnwer_key = _CONFIG % {
            "name": self._provider_name,
            "proto": self._proto,
        }
        variables = questions.load_answers(
            self.client,
            asnwer_key,
        )
        sso_bank = questions.QuestionBank(
            questions=self._questions,
            console=console,
            preseed=self._preseed,
            previous_answers=variables,
            show_hint=show_hint,
        )
        variables = self._ask(sso_bank, variables)
        questions.write_answers(self.client, asnwer_key, variables)


class _BaseExternalProviderStep(_BaseProviderStep):
    idp_name = "base"

    def __init__(self, *args):
        super().__init__(*args)
        self._openid_config = {
            "issuer_url": None,
            "client_id": None,
            "client_secret": None,
            "label": None,
            "provider_id": self._provider_name,
            "provider": "generic",
        }
        self._saml2_config = {
            "metadata-url": None,
            "label": None,
            "name": self._provider_name,
        }
        # dicts are passed by reference.
        self._config_map = {
            "openid": self._openid_config,
            "saml2": self._saml2_config,
        }
        self._url_cfg_key_map = {
            "openid": "issuer_url",
            "saml2": "metadata-url",
        }

        self._url_params = {}
        self._questions = copy.deepcopy(_QUESTIONS[self._proto][self.idp_name])

    def _get_preseed_map(self):
        preseed_map = {
            "openid": {
                "client_id": None,
                "client_secret": None,
                "label": None,
            },
            "saml2": {
                "label": None,
                "app_id": None,
            },
        }
        return preseed_map.get(self._proto, {})

    def _set_idp_metadata_url(self):
        key = self._url_cfg_key_map[self._proto]
        if not self._config_map[self._proto][key]:
            meta_url = _METADATA_URL_MAP[self._proto].get(self.idp_name)
            if not meta_url:
                raise click.ClickException(
                    f"cannot compose metadata URL for provider type {self.idp_name}"
                )
            self._config_map[self._proto][key] = meta_url % self._url_params

    def _ask_openid(self, q_bank: questions.QuestionBank, variables: dict):
        self._openid_config["client_id"] = q_bank.client_id.ask()
        self._openid_config["client_secret"] = q_bank.client_secret.ask()
        self._openid_config["label"] = q_bank.label.ask()

        required = [
            self._openid_config["client_id"],
            self._openid_config["client_secret"],
        ]
        if not all(required):
            raise click.ClickException("client_id and client_secret are mandatory")

        if not self._openid_config["label"]:
            label_name = self._provider_name.capitalize()
            self._openid_config["label"] = f"Log in with {label_name}"

        variables["label"] = self._openid_config["label"]
        variables["client_id"] = self._openid_config["client_id"]
        variables["client_secret"] = self._openid_config["client_secret"]
        return variables

    def _ask_saml2(self, q_bank: questions.QuestionBank, variables: dict):
        self._saml2_config["label"] = q_bank.label.ask()
        if not self._saml2_config["label"]:
            label_name = self._provider_name.capitalize()
            self._saml2_config["label"] = f"Log in with {label_name}"

        saml2_app_id = q_bank.app_id.ask()
        if not saml2_app_id:
            raise click.ClickException("app_id is mandatory")

        variables["app_id"] = saml2_app_id
        variables["label"] = self._saml2_config["label"]
        self._url_params["app_id"] = saml2_app_id

        return variables

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        ask_fn = getattr(self, f"_ask_{self._proto}", None)
        if not ask_fn:
            raise click.ClickException(f"invalid protocol {self._proto}")
        return ask_fn(q_bank, variables)

    @property
    def _charm_config(self):
        self._set_idp_metadata_url()
        cfg = self._config_map.get(self._proto, {})
        if not cfg:
            raise click.ClickException(f"No config possible for protocol {self._proto}")

        if not all(cfg.values()):
            raise click.ClickException("invalid state for provider step")
        return cfg

    def run(self, context: StepContext) -> Result:
        tfvars = _safe_get_tfvars(self.client)
        cfg = safe_get_sso_config(self.client)

        idp = cfg[self._proto].get(self._provider_name, None)
        if idp:
            cfg[self._proto][self._provider_name]["config"] = self._charm_config
        else:
            cfg[self._proto][self._provider_name] = {
                "config": self._charm_config,
                "provider_type": self._provider_type,
            }

        try:
            entity_id = _validate_idp(
                self._proto,
                self._provider_name,
                cfg[self._proto][self._provider_name],
            )
        except Exception as e:
            return Result(ResultType.FAILED, f"Failed to validate IDP {e}")

        if self._proto == "saml2" and entity_id:
            cfg[self._proto][self._provider_name]["remote_entity_id"] = entity_id

        for proto, providers in cfg.items():
            for provider, data in providers.items():
                if data.get("provider_type", None) == "canonical":
                    continue
                tfvars["sso-providers"][proto][provider] = data["config"]

        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, f"Failed to apply terraform plan {e}")

        update_config(self.client, SSO_CONFIG_KEY, cfg)
        update_config(self.client, CONFIG_KEY, tfvars)

        charm_name = f"keystone-idp-{self._proto}-{self._provider_name}"
        apps = ["keystone", "horizon", charm_name]
        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, context.status)
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
    idp_name = "google"

    def __init__(self, *args, **kw):
        super().__init__(
            "Add google external IDP",
            "Adding google external IDP",
            "google",
            *args,
            **kw,
        )


class AddOktaProviderStep(_BaseExternalProviderStep):
    idp_name = "okta"

    def __init__(self, *args, **kw):
        super().__init__(
            "Add okta external IDP",
            "Adding okta external IDP",
            "okta",
            *args,
            **kw,
        )

    def _get_preseed_map(self):
        preseed = super()._get_preseed_map()
        preseed["okta_org"] = None
        return preseed

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask(q_bank, variables)
        okta_org = q_bank.okta_org.ask()
        if not okta_org:
            raise click.ClickException("okta_org is mandatory")
        self._url_params["okta_org"] = okta_org
        variables["okta_org"] = okta_org
        return variables


class AddEntraProviderStep(_BaseExternalProviderStep):
    idp_name = "entra"

    def __init__(self, *args, **kw):
        super().__init__(
            "Add entra external IDP",
            "Adding entra external IDP",
            "entra",
            *args,
            **kw,
        )

    def _get_preseed_map(self):
        preseed = super()._get_preseed_map()
        preseed["microsoft_tenant"] = None
        return preseed

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask(q_bank, variables)
        tenant_id = q_bank.microsoft_tenant.ask()
        if not tenant_id:
            raise click.ClickException("microsoft_tenant is mandatory")
        self._url_params["microsoft_tenant"] = tenant_id
        variables["microsoft_tenant"] = tenant_id
        return variables


class AddGenericProviderStep(_BaseExternalProviderStep):
    idp_name = "generic"

    def __init__(self, *args, **kw):
        super().__init__(
            "Add generic external IDP",
            "Adding generic external IDP",
            "generic",
            *args,
            **kw,
        )

    def _get_preseed_map(self):
        preseed = {
            "openid": {
                "client_id": None,
                "client_secret": None,
                "label": None,
                "issuer_url": None,
            },
            "saml2": {"label": None, "metadata_url": None, "ca_chain": ""},
        }
        return preseed[self._proto]

    def _ask_openid(self, q_bank: questions.QuestionBank, variables: dict):
        variables = super()._ask_openid(q_bank, variables)
        issuer_url = q_bank.issuer_url.ask()
        if not issuer_url:
            raise click.ClickException("issuer_url is mandatory")
        variables["issuer_url"] = issuer_url
        self._openid_config["issuer_url"] = issuer_url
        return variables

    def _ask_saml2(self, q_bank: questions.QuestionBank, variables: dict):
        self._saml2_config["label"] = q_bank.label.ask()
        if not self._saml2_config["label"]:
            label_name = self._provider_name.capitalize()
            self._saml2_config["label"] = f"Log in with {label_name}"

        metadata_url = q_bank.metadata_url.ask()
        if not metadata_url:
            raise click.ClickException("metadata-url is mandatory")

        ca_chain = q_bank.ca_chain.ask()
        if ca_chain:
            self._saml2_config["ca-chain"] = ca_chain
        else:
            self._saml2_config["ca-chain"] = ""

        self._saml2_config["metadata-url"] = metadata_url

        variables["metadata_url"] = metadata_url
        variables["ca_chain"] = self._saml2_config["ca-chain"]
        variables["label"] = self._saml2_config["label"]

        return variables

    def _ask(self, q_bank: questions.QuestionBank, variables: dict):
        ask_fn = getattr(self, f"_ask_{self._proto}", None)
        if not ask_fn:
            raise click.ClickException(f"unsupported protocol {self._proto}")
        return ask_fn(q_bank, variables)


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

    def run(self, context: StepContext) -> Result:
        """Run configure steps."""
        cfg = safe_get_sso_config(self.client)

        idp = cfg.get(self._provider_name)
        if idp:
            cfg[self._provider_name]["config"] = self._charm_config
        else:
            cfg[self._provider_name] = {
                "config": self._charm_config,
                "provider_type": self._provider_type,
                "provider_proto": self._proto,
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


class ValidateIdentityManifest(BaseStep):
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
        if provider == "generic":
            return config["issuer_url"]
        else:
            issuer_url_tpl = _METADATA_URL_MAP["openid"].get(provider, None)
            if issuer_url_tpl is None:
                raise ValueError(f"Invalid provider type {provider}")
            issuer_url = issuer_url_tpl % config
            return issuer_url

    def _charm_config_openid(
        self, name: str, provider: str, config: dict[str, str | None]
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

    def _charm_config_saml2(
        self, name: str, provider: str, config: dict[str, str | None]
    ):
        if not config.get("label"):
            config["label"] = f"Log in with {name}"
        metadata_url = None
        if provider == "generic":
            metadata_url = config.get("metadata-url", None)
        else:
            metadata_url_tpl = _METADATA_URL_MAP["saml2"].get(provider, None)
            if metadata_url_tpl is None:
                raise ValueError(f"Invalid provider type {provider}")
            metadata_url = metadata_url_tpl % config
        if not metadata_url:
            raise ValueError(f"Could not detemine metadata-url for provider {provider}")

        conf = {
            "metadata-url": metadata_url,
            "name": name,
            "label": config["label"],
        }
        chain = config.get("ca_chain", "")
        if chain:
            conf["ca-chain"] = chain

        return conf

    def _charm_config(
        self,
        name: str,
        provider: str,
        protocol: str,
        config: dict[str, str | None],
    ):
        cfg_map = {
            "openid": self._charm_config_openid,
            "saml2": self._charm_config_saml2,
        }
        if protocol not in cfg_map:
            raise ValueError(f"Invalid protocol {protocol}")
        return cfg_map[protocol](name, provider, config)

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
        questions = _QUESTIONS.get(protocol, {}).get(provider, None)
        if not questions:
            raise click.ClickException(
                f"Unknown provider type {provider} ({protocol}) for {name}"
            )

        missing_keys = []
        norm_config: dict[str, str | None] = {}
        for key in questions:
            norm_key = key.replace("-", "_")
            cfg_val = config.get(
                norm_key,
                config.get(
                    norm_key.replace("_", "-"),
                    None,
                ),
            )
            if not cfg_val and key != "ca_chain":
                missing_keys.append(key)

            if key == "ca_chain" and not cfg_val:
                norm_config["ca-chain"] = ""
                continue
            norm_config[norm_key] = cfg_val

        if missing_keys:
            raise click.ClickException(
                f"Missing config for provider {name} ({provider}): "
                f"{', '.join(missing_keys)}"
            )

        parsed_conf = self._charm_config(name, provider, protocol, norm_config)
        _validate_idp(protocol, name, {"config": parsed_conf})
        return parsed_conf

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.manifest or not self.manifest.core.config.identity:
            return Result(ResultType.SKIPPED)

        if not self.manifest.core.config.identity.profiles:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        cfg = safe_get_sso_config(self.client)

        if not self.manifest:
            return Result(ResultType.COMPLETED)

        if not self.manifest.core.config.identity:
            return Result(ResultType.COMPLETED)

        profiles = self.manifest.core.config.identity.profiles
        if not profiles:
            return Result(ResultType.COMPLETED)

        try:
            for name, config in profiles.items():
                if config.protocol not in VALID_SSO_PROTOCOLS:
                    raise ValueError(
                        f"Invalid protocol {config.protocol} for profile "
                        f"{name} (Valid protocols: "
                        f"{', '.join(VALID_SSO_PROTOCOLS)})"
                    )
                cfg[config.protocol][name] = {
                    "provider_type": config.provider,
                }
                if config.provider == "canonical":
                    if config.protocol != "openid":
                        continue
                    cfg[config.protocol][name]["config"] = self._canonical_charm_config(
                        name,
                        config.provider,
                        config.protocol,
                        config.config,
                    )
                else:
                    cfg[config.protocol][name]["config"] = self._external_charm_config(
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

    def is_skip(self, context: StepContext) -> Result:
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

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        tfvars = _safe_get_tfvars(self.client)
        cfg = safe_get_sso_config(self.client)

        apps = ["keystone", "horizon"]
        canonical_providers: dict[str, dict[str, dict]] = {
            "openid": {},
            # SAML2 is not yet supported by canonical identity platform,
            # but leaving room for later augmentations.
            "saml2": {},
        }
        for proto, data in cfg.items():
            for provider, conf in data.items():
                if conf.get("provider_type", None) == "canonical":
                    canonical_providers[proto][provider] = conf
                    continue
                if "ca-chain" in conf["config"]:
                    if not conf["config"]["ca-chain"]:
                        conf["config"].pop("ca-chain", None)
                tfvars["sso-providers"][proto][provider] = conf["config"]
                apps.append(f"keystone-idp-{proto}-{provider}")
        self.tfhelper.write_tfvars(tfvars)
        update_config(self.client, CONFIG_KEY, tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        app_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, app_queue, context.status)
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

        # We don't yet know what future SAML2 support will look like
        # in Canonical Identity Platform. We don't know if we'll have saml
        # as part of the same offer or a different offer, implemented by
        # a different charm. We reference "openid" here explicitly for
        # now.
        for provider, data in canonical_providers["openid"].items():
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


class SetKeystoneSAMLCertAndKeyStep(BaseStep, JujuStepHelper):
    """Deploy identity providers on bootstrap."""

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest | None = None,
        x509_cert: str = "",
        x509_key: str = "",
    ):
        super().__init__(
            "Identity",
            "Setting Keystone SP SAML2 certificate and key",
        )
        self.client = deployment.get_client()
        self.manifest = manifest
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.x509_cert = x509_cert
        self.x509_key = x509_key

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.manifest and not all([self.x509_cert, self.x509_key]):
            return Result(ResultType.SKIPPED)

        if all([self.x509_cert, self.x509_key]):
            return Result(ResultType.COMPLETED)

        if not self._cert_and_key_from_manifest():
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def _cert_and_key_from_manifest(self) -> Mapping[str, str]:
        if not self.manifest:
            return {}

        identity = self.manifest.core.config.identity
        if not identity:
            return {}

        has_manifest = all(
            [
                identity.saml2_x509.certificate,
                identity.saml2_x509.key,
            ],
        )
        if not has_manifest:
            return {}

        return {
            "cert": identity.saml2_x509.certificate,
            "key": identity.saml2_x509.key,
        }

    def _get_cert_and_key_from_params(self) -> Mapping[str, str]:
        if all([self.x509_cert, self.x509_key]):
            return {
                "cert": self.x509_cert,
                "key": self.x509_key,
            }
        cert_details = self._cert_and_key_from_manifest()
        return cert_details

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        cert_and_key = self._get_cert_and_key_from_params()
        try:
            cert_data = open(cert_and_key["cert"]).read()
            key_data = open(cert_and_key["key"]).read()
        except Exception as e:
            return Result(ResultType.FAILED, str(e))

        if not cert_and_key_match(cert_data.encode(), key_data.encode()):
            raise ValueError(
                f"Certificate {cert_and_key['cert']} is not derived from "
                f"{cert_and_key['key']}"
            )

        try:
            saml2_config = read_config(self.client, _SAML2_CONFIG_KEY)
        except ConfigItemNotFoundException:
            saml2_config = {}

        saml_secret_id = saml2_config.get("saml2_cert_key_secret", None)

        if saml_secret_id:
            k_secret = self.jhelper.get_secret(
                OPENSTACK_MODEL,
                saml_secret_id,
            )
        else:
            saml_secret_id = self.jhelper.add_secret(
                info="Keystone SAML SP x509 key",
                model=OPENSTACK_MODEL,
                name=_SAML2_CERT_KEY_SECRET,
                data={
                    "certificate": cert_data,
                    "key": key_data,
                },
            )
            saml2_config["saml2_cert_key_secret"] = saml_secret_id
            update_config(self.client, _SAML2_CONFIG_KEY, saml2_config)

            k_secret = self.jhelper.get_secret(
                OPENSTACK_MODEL,
                saml_secret_id,
            )

        k_cert = k_secret.get("certificate", None)
        k_key = k_secret.get("key", None)
        if cert_data != k_cert or key_data != k_key:
            self.jhelper.update_secret(
                model=OPENSTACK_MODEL,
                name=saml_secret_id,
                data={
                    "certificate": cert_data,
                    "key": key_data,
                },
            )

        # Grant secret access to the keystone application
        self.jhelper.grant_secret(OPENSTACK_MODEL, saml_secret_id, "keystone")

        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars["saml-x509-keypair"] = f"secret:{saml_secret_id}"

        update_config(self.client, CONFIG_KEY, tfvars)
        self.tfhelper.write_tfvars(tfvars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                ["keystone"],
                timeout=APPLICATION_REMOVE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
