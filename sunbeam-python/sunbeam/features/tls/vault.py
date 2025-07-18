# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import typing
from pathlib import Path

import click
import pydantic
import yaml
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from rich.table import Table

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core import questions
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    Result,
    ResultType,
    SunbeamException,
    read_config,
    run_plan,
    str_presenter,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import AddManifestStep, FeatureConfig
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.utils import (
    encode_base64_as_string,
    get_subject_from_csr,
    is_certificate_valid,
    validate_ca_certificate,
    validate_ca_chain,
)
from sunbeam.features.interface.v1.openstack import (
    TerraformPlanLocation,
    WaitForApplicationsStep,
)
from sunbeam.features.tls.common import (
    INGRESS_CHANGE_APPLICATION_TIMEOUT,
    TlsFeature,
    TlsFeatureConfig,
    certificate_questions,
    get_outstanding_certificate_requests,
)
from sunbeam.features.vault.feature import VaultCommandFailedException, VaultHelper
from sunbeam.steps.k8s import TRAEFIK_CONFIG_KEY
from sunbeam.utils import click_option_show_hints, pass_method_obj

CERTIFICATE_FEATURE_KEY = "TlsProvider"
CA_APP_NAME = "vault"
LOG = logging.getLogger(__name__)
console = Console()
ConfigType = typing.TypeVar("ConfigType", bound=FeatureConfig)


class _Certificate(pydantic.BaseModel):
    certificate: str


class VaultTlsFeatureConfig(TlsFeatureConfig):
    certificates: dict[str, _Certificate] = {}


class ConfigureVaultCAStep(BaseStep):
    """Configure CA certificates."""

    _CONFIG = "FeatureCACertificatesConfig"

    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        ca_cert: str,
        ca_chain: str,
        deployment_preseed: dict | None = None,
    ):
        super().__init__("Configure CA certs", "Configuring CA certificates")
        self.client = client
        self.jhelper = jhelper
        self.ca_cert = ca_cert
        self.ca_chain = ca_chain
        self.preseed = deployment_preseed or {}
        self.app = "manual-tls-certificates"
        self.model = OPENSTACK_MODEL
        self.process_certs: dict = {}

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Prompt the user for certificates.

        Prompts the user for required information for cert configuration.

        :param console: the console to prompt on
        :type console: rich.console.Console (Optional)
        """
        action_cmd = "get-outstanding-certificate-requests"
        # let exception propagate, since they are SunbeamException
        # they will be caught cleanly
        action_result = get_outstanding_certificate_requests(
            self.app, OPENSTACK_MODEL, self.jhelper
        )

        LOG.debug(f"Result from action {action_cmd}: {action_result}")
        if action_result.get("return-code", 0) > 1:
            raise click.ClickException(
                "Unable to get outstanding certificate requests from CA"
            )

        certs_to_process = json.loads(action_result.get("result", "[]"))
        if not certs_to_process:
            LOG.debug("No outstanding certificates to process")
            return

        variables = questions.load_answers(self.client, self._CONFIG)
        variables.setdefault("certificates", {})
        self.preseed.setdefault("certificates", {})

        for record in certs_to_process:
            csr = record.get("csr")
            app = record.get("application_name")
            relation_id = record.get("relation_id")
            unit_name = record.get("unit_name")
            if not unit_name:
                unit_name = (
                    f"{self.jhelper.get_leader_unit(CA_APP_NAME, OPENSTACK_MODEL)}"
                )

            # Each unit can have multiple CSRs
            subject = get_subject_from_csr(csr)
            if not subject:
                raise click.ClickException(f"Not a valid CSR for unit {unit_name}")

            cert_questions = certificate_questions(unit_name, subject)
            certificates_bank = questions.QuestionBank(
                questions=cert_questions,
                console=console,
                preseed=self.preseed.get("certificates", {}).get(subject),
                previous_answers=variables.get("certificates", {}).get(subject),
                show_hint=show_hint,
            )
            cert = certificates_bank.certificate.ask()
            if not cert or not is_certificate_valid(cert):
                raise click.ClickException("Not a valid certificate")

            self.process_certs[subject] = {
                "app": app,
                "unit": unit_name,
                "relation_id": relation_id,
                "csr": csr,
                "certificate": cert,
            }
            variables["certificates"].setdefault(subject, {})
            variables["certificates"][subject]["certificate"] = cert

        questions.write_answers(self.client, self._CONFIG, variables)

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run configure steps."""
        action_cmd = "provide-certificate"
        try:
            unit = self.jhelper.get_leader_unit(self.app, OPENSTACK_MODEL)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {self.app} leader")
            return Result(ResultType.FAILED, str(e))

        LOG.debug(f"Process certs: {self.process_certs}")
        for subject, request in self.process_certs.items():
            csr = request.get("csr")
            csr = encode_base64_as_string(csr)
            if not csr:
                return Result(ResultType.FAILED)

            action_params = {
                "certificate": request.get("certificate"),
                "ca-chain": self.ca_cert,
                "ca-certificate": self.ca_cert,
                "certificate-signing-request": str(csr),
            }

            LOG.debug(f"Running action {action_cmd}")
            try:
                action_result = self.jhelper.run_action(
                    unit, OPENSTACK_MODEL, action_cmd, action_params
                )
            except ActionFailedException as e:
                LOG.debug(f"Running action {action_cmd} on {unit}")
                return Result(ResultType.FAILED, str(e))

            LOG.debug(f"Result from action {action_cmd}: {action_result}")
            if action_result.get("return-code", 0) > 1:
                return Result(
                    ResultType.FAILED, f"Action {action_cmd} on {unit} returned error"
                )

        return Result(ResultType.COMPLETED)


class VaultTlsFeature(TlsFeature):
    version = Version("0.0.1")

    name = "tls.vault"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    @click.group()
    def vault_group(self) -> None:
        """Manage CA (HashiCorp Vault)."""

    def config_type(self) -> type | None:
        """Return the config type for the feature."""
        return VaultTlsFeatureConfig

    def preseed_questions_content(self) -> list:
        """Generate preseed manifest content."""
        certificate_question_bank = questions.QuestionBank(
            questions=certificate_questions("unit", "subject"),
            console=console,
            previous_answers={},
        )
        content = questions.show_questions(
            certificate_question_bank,
            section="certificates",
            subsection="<CSR x500UniqueIdentifier>",
            section_description="TLS Certificates",
            comment_out=True,
        )
        return [content]

    def _build_tls_config_maps(
        self,
        jhelper: JujuHelper,
        endpoints: list[str],
    ) -> dict[str, dict[str, str]]:
        """Get the TLS config maps for specified endpoints."""
        external: dict[str, str] = {}
        missing: list[str] = []

        try:
            traefik_vars = read_config(self.client, TRAEFIK_CONFIG_KEY)
            traefik_endpoints = traefik_vars.get("traefik-endpoints", {})
        except ConfigItemNotFoundException:
            raise click.ClickException(
                "Traefik endpoint hostnames are not configured in Sunbeam. "
                "Please configure them before proceeding."
            )

        endpoint_key_map = {
            "public": "traefik-public",
            "internal": "traefik",
            "rgw": "traefik-rgw",
        }

        for ep in endpoints:
            endpoint_key = endpoint_key_map[ep]
            hostname = traefik_endpoints.get(endpoint_key)
            if hostname:
                external[ep] = hostname
            else:
                missing.append(ep)

        if missing:
            raise click.ClickException(
                "TLS Vault cannot be enabled because the following endpoints "
                f"are missing external hostnames: {', '.join(missing)}. "
                "Please configure these hostnames using Sunbeam bootstrap."
            )

        maps: dict[str, dict[str, str]] = {}
        domains = set()
        for hostname in external.values():
            if "." in hostname:
                domains.add(hostname.split(".", 1)[1])
        if len(domains) != 1:
            raise click.ClickException(
                "Traefik endpoints must share one common domain; found multiple: "
                f"{', '.join(domains)}"
            )

        common_domain = domains.pop()
        maps["vault-config"] = {"common_name": common_domain}

        if "public" in external:
            maps["traefik-public-config"] = {"external_hostname": external["public"]}
        if "internal" in external:
            maps["traefik-config"] = {"external_hostname": external["internal"]}
        if "rgw" in external:
            maps["traefik-rgw-config"] = {"external_hostname": external["rgw"]}

        return maps

    @click.command()
    @click.option(
        "--endpoint",
        "endpoints",
        multiple=True,
        default=["public"],
        type=click.Choice(["public", "internal", "rgw"], case_sensitive=False),
        help="Specify which endpoints to apply TLS for.",
    )
    @click.option(
        "--ca-chain",
        required=True,
        type=str,
        callback=validate_ca_chain,
        help="Base64 encoded CA Chain certificate",
    )
    @click.option(
        "--ca",
        required=True,
        type=str,
        callback=validate_ca_certificate,
        help="Base64 encoded CA certificate",
    )
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self,
        deployment: Deployment,
        ca: str,
        ca_chain: str,
        endpoints: list[str],
        show_hints: bool,
    ):
        """Enable TLS Vault feature."""
        self.client = deployment.get_client()

        config = VaultTlsFeatureConfig(
            ca=ca,
            ca_chain=ca_chain,
            endpoints=endpoints,
        )
        self.enable_feature(deployment, config, show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool):
        """Disable TLS Vault feature."""
        self.disable_feature(deployment, show_hints)

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return ["manual-tls-certificates"]

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: VaultTlsFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        tfvars: dict[str, typing.Any] = {
            "traefik-to-tls-provider": CA_APP_NAME,
            "manual-tls-certificates-channel": "1/stable",
        }
        jhelper = JujuHelper(deployment.juju_controller)
        tfvars.update(self._build_tls_config_maps(jhelper, config.endpoints))
        tfvars["enable-tls-for-public-endpoint"] = "public" in config.endpoints
        tfvars["enable-tls-for-internal-endpoint"] = "internal" in config.endpoints
        tfvars["enable-tls-for-rgw-endpoint"] = "rgw" in config.endpoints
        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        tfvars: dict[str, None | str | bool] = {"traefik-to-tls-provider": None}
        provider_config = self.provider_config(deployment)
        endpoints = provider_config.get("endpoints", [])

        # Remove Traefik endpoints external hostnames
        if "public" in endpoints:
            tfvars.update(
                {"enable-tls-for-public-endpoint": False, "traefik-public-config": None}
            )
        if "internal" in endpoints:
            tfvars.update(
                {"enable-tls-for-internal-endpoint": False, "traefik-config": None}
            )
        if "rgw" in endpoints:
            tfvars.update(
                {"enable-tls-for-rgw-endpoint": False, "traefik-rgw-config": None}
            )

        # Remove Vault common_name
        tfvars.update({"vault-config": None})

        return tfvars

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    @click.option(
        "--format",
        type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
        default=FORMAT_TABLE,
        help="Output format",
    )
    @pass_method_obj
    def list_outstanding_csrs(self, deployment: Deployment, format: str) -> None:
        """List outstanding CSRs."""
        app = "manual-tls-certificates"
        model = OPENSTACK_MODEL
        action_cmd = "get-outstanding-certificate-requests"
        jhelper = JujuHelper(deployment.juju_controller)
        try:
            action_result = get_outstanding_certificate_requests(app, model, jhelper)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {app} leader to print CSRs")
            raise click.ClickException(str(e))
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} failed")
            raise click.ClickException(str(e))

        LOG.debug(f"Result from action {action_cmd}: {action_result}")
        if action_result.get("return-code", 0) > 1:
            raise click.ClickException(
                "Unable to get outstanding certificate requests from CA"
            )

        certs_to_process = json.loads(action_result.get("result", "[]"))
        csrs = {
            unit: csr
            for record in certs_to_process
            if (
                unit := (
                    str(
                        record.get("unit_name")
                        or jhelper.get_leader_unit(CA_APP_NAME, OPENSTACK_MODEL)
                    )
                )
            )
            and (csr := record.get("csr"))
        }

        if format == FORMAT_TABLE:
            table = Table()
            table.add_column("Unit name")
            table.add_column("CSR")
            for relation, csr in csrs.items():
                table.add_row(relation, csr)
            console.print(table)
        elif format == FORMAT_YAML:
            yaml.add_representer(str, str_presenter)
            console.print(yaml.dump(csrs))

    @click.command()
    @click.option(
        "-m",
        "--manifest",
        "manifest_path",
        help="Manifest file.",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    @click_option_show_hints
    @pass_method_obj
    def configure(
        self,
        deployment: Deployment,
        manifest_path: Path | None = None,
        show_hints: bool = False,
    ) -> None:
        """Configure Unit certs."""
        client = deployment.get_client()
        manifest = deployment.get_manifest(manifest_path)
        preseed = {}
        if (ca := manifest.get_feature(self.name.split(".")[-1])) and ca.config:
            preseed = ca.config.model_dump(by_alias=True)
        model = OPENSTACK_MODEL
        apps_to_monitor = [CA_APP_NAME]

        try:
            config = read_config(client, CERTIFICATE_FEATURE_KEY)
        except ConfigItemNotFoundException:
            config = {}
        ca = config.get("ca")
        ca_chain = config.get("chain")

        if ca is None or ca_chain is None:
            raise click.ClickException("CA and CA Chain not configured")

        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            AddManifestStep(client, manifest_path),
            ConfigureVaultCAStep(
                client,
                jhelper,
                ca,
                ca_chain,
                deployment_preseed=preseed,
            ),
            # On ingress change, the keystone takes time to update the service
            # endpoint, update the identity-service relation data on every
            # related application.
            WaitForApplicationsStep(
                jhelper, apps_to_monitor, model, INGRESS_CHANGE_APPLICATION_TIMEOUT
            ),
        ]
        run_plan(plan, console, show_hints)
        click.echo("CA certs configured")

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "init": [{"name": self.group.name, "command": self.tls_group}],
            "init.tls": [{"name": "vault", "command": self.vault_group}],
            "init.tls.vault": [
                {"name": "unit_certs", "command": self.configure},
                {
                    "name": "list_outstanding_csrs",
                    "command": self.list_outstanding_csrs,
                },
            ],
        }

    def is_vault_application_active(self, jhelper: JujuHelper) -> bool:
        """Check if Vault is deployed, initialized, and authorized."""
        try:
            leader = jhelper.get_leader_unit(CA_APP_NAME, OPENSTACK_MODEL)
        except SunbeamException:
            raise click.ClickException(
                "Cannot enable TLS Vault because Vault is not deployed. "
                "Please deploy Vault first."
            )

        app_status = jhelper.get_application(CA_APP_NAME, OPENSTACK_MODEL)
        raw_units: typing.Any = app_status.units
        if hasattr(raw_units, "items"):
            unit_items = raw_units.items()
        else:
            unit_items = enumerate(raw_units)
        units = list(unit_items)
        if not units:
            raise click.ClickException(
                "Vault application has no units. Please deploy Vault first."
            )
        _, unit_stat = units[0]

        status = unit_stat.workload_status.current
        message = unit_stat.workload_status.message

        if status == "active":
            return True

        vhelper = VaultHelper(jhelper)
        try:
            vault_status = vhelper.get_vault_status(leader)
        except VaultCommandFailedException as e:
            raise click.ClickException(f"Error querying Vault status: {e}")
        except (TimeoutError, JujuException) as e:
            raise click.ClickException(f"Unable to contact Vault: {e}")

        if not vault_status.get("initialized", False):
            raise click.ClickException(
                "Vault is deployed but uninitialized. "
                "Please run `sunbeam vault init` first."
            )
        if vault_status.get("sealed", True):
            raise click.ClickException(
                "Vault is initialized but still sealed. "
                "Please unseal Vault before proceeding."
            )

        # There is a case where after vault unseal, the vault
        # application is still in blocked state, but shows the message
        # "Please initialize Vault or integrate
        # with an auto-unseal provider"
        if "authorize" in message.lower() or "initialize" in message.lower():
            raise click.ClickException(
                "Vault is not authorized. Please run `sunbeam vault authorize-charm`"
                "first."
            )

        if status == "blocked":
            raise click.ClickException(f"Vault is blocked: {message}")
        return True

    def pre_enable(
        self,
        deployment: Deployment,
        config: TlsFeatureConfig,
        show_hints: bool,
    ) -> None:
        """Handler to perform tasks before enabling the feature."""
        super().pre_enable(deployment, config, show_hints)

        jhelper = JujuHelper(deployment.juju_controller)
        if not self.is_vault_application_active(jhelper):
            raise click.ClickException(
                "Cannot enable TLS Vault as Vault is not enabled. Enable Vault first."
            )

        try:
            tfvars = read_config(self.client, TRAEFIK_CONFIG_KEY)
            saved = tfvars.get("traefik-endpoints", {})
        except ConfigItemNotFoundException:
            raise click.ClickException(
                "Traefik endpoint hostnames are not configured in Sunbeam. "
                "Please configure them using the bootstrap prompts."
            )

        key_map = {
            "public": "traefik-public",
            "internal": "traefik",
            "rgw": "traefik-rgw",
        }
        external: dict[str, str] = {}
        missing: list[str] = []
        for ep in config.endpoints:
            k = key_map[ep]
            h = saved.get(k, "").strip()
            if h:
                external[ep] = h
            else:
                missing.append(ep)

        if missing:
            raise click.ClickException(
                "TLS Vault cannot be enabled because no hostname was provided for: "
                f"{', '.join(missing)}"
            )

        domains = set()
        for hostname in external.values():
            if "." in hostname:
                domains.add(hostname.split(".", 1)[1])
        if len(domains) != 1:
            raise click.ClickException(
                f"Traefik endpoints must share one domain; \
                found: {', '.join(external.values())}"
            )
        common_domain = domains.pop()
        tfvars.update({"vault-config": {"common_name": common_domain}})
        console.print(f"Set {CA_APP_NAME}.common_name = {common_domain}")
