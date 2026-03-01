# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import binascii
import json
import logging

import click
import pydantic
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core import questions
from sunbeam.core.ceph import is_microceph_necessary
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import (
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.utils import (
    encode_base64_as_string,
    generate_ca_chain,
    get_subject_from_csr,
    is_certificate_valid,
)
from sunbeam.features.interface.v1.base import BaseFeatureGroup
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    WaitForApplicationsStep,
)
from sunbeam.utils import pass_method_obj

CERTIFICATE_FEATURE_KEY = "TlsProvider"
CA_MANUAL_TLS_CERTIFICATE = "manual-tls-certificates"
CA_MANUAL_TLS_CERTIFICATE_INTERFACE = "certificates"
# Time out for keystone to settle once ingress change relation data
INGRESS_CHANGE_APPLICATION_TIMEOUT = 1800
LOG = logging.getLogger(__name__)
console = Console()


class TlsFeatureConfig(FeatureConfig):
    ca: str | None = None
    ca_chain: str | None = None
    endpoints: list[str] = pydantic.Field(default_factory=list)


class TlsFeatureGroup(BaseFeatureGroup):
    name = "tls"

    @click.group()
    @pass_method_obj
    def enable_group(self, deployment: Deployment) -> None:
        """Enable tls group."""

    @click.group()
    @pass_method_obj
    def disable_group(self, deployment: Deployment) -> None:
        """Disable TLS group."""


class TlsFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")
    group = TlsFeatureGroup

    def ca_cert_name(self, region: str | None) -> str:
        """CA Cert name to be used to add to keystone."""
        # Keystone lists ca cert names with any .'s replaced
        # with -.
        # https://opendev.org/openstack/sunbeam-charms/src/commit/c8761241f3b7be381101fbe5942aa2174daf1797/charms/keystone-k8s/src/charm.py#L629
        region = region or "RegionOne"
        return self.feature_key.replace(".", "-") + f"-{region}"

    @click.group()
    def enable_tls(self) -> None:
        """Enable TLS group."""

    @click.group()
    def disable_tls(self) -> None:
        """Disable TLS group."""

    @click.group()
    def tls_group(self):
        """Manage TLS."""

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "manual-tls-certificates": CharmManifest(
                    channel="latest/stable",
                )
            }
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "manual-tls-certificates": {
                        "channel": "manual-tls-certificates-channel",
                        "revision": "manual-tls-certificates-revision",
                        "config": "manual-tls-certificates-config",
                    }
                }
            }
        }

    def provider_config(self, deployment: Deployment) -> dict:
        """Return stored provider configuration."""
        try:
            provider_config = read_config(
                deployment.get_client(), CERTIFICATE_FEATURE_KEY
            )
        except ConfigItemNotFoundException:
            provider_config = {}
        return provider_config

    def pre_enable(
        self, deployment: Deployment, config: TlsFeatureConfig, show_hints: bool
    ) -> None:
        """Handler to perform tasks before enabling the feature."""
        super().pre_enable(deployment, config, show_hints)

        provider_config = self.provider_config(deployment)

        provider = provider_config.get("provider")
        if provider and provider != self.name:
            raise Exception(f"Certificate provider already set to {provider!r}")

    def post_enable(
        self, deployment: Deployment, config: TlsFeatureConfig, show_hints: bool
    ) -> None:
        """Handler to perform tasks after the feature is enabled."""
        if deployment.region_ctrl_juju_controller:
            # This is a secondary region, Keystone is expected to run in the
            # primary region.
            jhelper = JujuHelper(deployment.region_ctrl_juju_controller)
        else:
            jhelper = JujuHelper(deployment.juju_controller)
        plan: list[BaseStep] = [
            AddCACertsToKeystoneStep(
                jhelper,
                self.ca_cert_name(deployment.get_region_name()),
                config.ca,  # type: ignore
                config.ca_chain,  # type: ignore
            )
        ]
        run_plan(plan, console, show_hints)

        stored_config = {
            "provider": self.name,
            "ca": config.ca,
            "chain": config.ca_chain,
            "endpoints": config.endpoints,
        }
        update_config(deployment.get_client(), CERTIFICATE_FEATURE_KEY, stored_config)

    def pre_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks before disabling the feature."""
        super().pre_disable(deployment, show_hints)

        provider_config = self.provider_config(deployment)

        provider = provider_config.get("provider")
        if provider and provider != self.name:
            raise Exception(f"Certificate provider already set to {provider!r}")

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks after the feature is disabled."""
        super().post_disable(deployment, show_hints)

        client = deployment.get_client()

        jhelper_current = deployment.get_juju_helper()
        jhelper_keystone = deployment.get_juju_helper(keystone=True)

        model = OPENSTACK_MODEL
        apps_to_monitor = ["traefik", "traefik-public", "keystone"]
        if client.cluster.list_nodes_by_role("storage") and is_microceph_necessary(
            client
        ):
            apps_to_monitor.append("traefik-rgw")

        plan: list[BaseStep] = [
            RemoveCACertsFromKeystoneStep(
                jhelper_keystone,
                self.ca_cert_name(deployment.get_region_name()),
                self.feature_key,
            ),
            WaitForApplicationsStep(
                jhelper_current,
                apps_to_monitor,
                model,
                INGRESS_CHANGE_APPLICATION_TIMEOUT,
            ),
        ]
        run_plan(plan, console, show_hints)

        config: dict = {}
        update_config(deployment.get_client(), CERTIFICATE_FEATURE_KEY, config)


class AddCACertsToKeystoneStep(BaseStep):
    """Transfer CA certificates."""

    def __init__(
        self,
        jhelper: JujuHelper,
        name: str,
        ca_cert: str,
        ca_chain: str | None = None,
    ):
        super().__init__(
            "Transfer CA certs to keystone", "Transferring CA certificates to keystone"
        )
        self.jhelper = jhelper
        self.cert_name = name.lower()
        self.ca_cert = ca_cert
        self.ca_chain = ca_chain
        self.app = "keystone"
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        action_cmd = "list-ca-certs"
        try:
            unit = self.jhelper.get_leader_unit(self.app, self.model)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {self.app} leader")
            return Result(ResultType.FAILED, str(e))

        try:
            action_result = self.jhelper.run_action(unit, self.model, action_cmd)
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} on {unit} failed")
            return Result(ResultType.FAILED, str(e))

        LOG.debug(f"Result from action {action_cmd}: {action_result}")

        ca_list = action_result
        if self.cert_name in ca_list:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run keystone add-ca-certs action."""
        action_cmd = "add-ca-certs"
        try:
            unit = self.jhelper.get_leader_unit(self.app, self.model)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {self.app} leader")
            return Result(ResultType.FAILED, str(e))

        action_params = {
            "name": self.cert_name,
            "ca": self.ca_cert,
        }
        if self.ca_chain:
            action_params["chain"] = self.ca_chain

        try:
            LOG.debug(f"Running action {action_cmd} with params {action_params}")
            self.jhelper.run_action(unit, self.model, action_cmd, action_params)
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} on {unit} failed")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveCACertsFromKeystoneStep(BaseStep):
    """Remove CA certificates."""

    def __init__(
        self,
        jhelper: JujuHelper,
        name: str,
        feature_key: str,
    ):
        super().__init__(
            "Remove CA certs from keystone", "Removing CA certificates from keystone"
        )
        self.jhelper = jhelper
        self.cert_name = name.lower()
        self.feature_key = feature_key.lower()
        self.app = "keystone"
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        action_cmd = "list-ca-certs"
        try:
            unit = self.jhelper.get_leader_unit(self.app, self.model)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {self.app} leader")
            return Result(ResultType.FAILED, str(e))

        try:
            action_result = self.jhelper.run_action(unit, self.model, action_cmd)
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} on {unit} failed")
            return Result(ResultType.FAILED, str(e))

        LOG.debug(f"Result from action {action_cmd}: {action_result}")

        ca_list = action_result
        # Replace any dot with hyphen in ca cert name.
        # self.cert_name is ensured not to have dot, however to maintain backward
        # compatability this is needed.
        if self.cert_name.replace(".", "-") not in ca_list:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run keystone add-ca-certs action."""
        action_cmd = "remove-ca-certs"
        try:
            unit = self.jhelper.get_leader_unit(self.app, self.model)
        except LeaderNotFoundException as e:
            LOG.debug(f"Unable to get {self.app} leader")
            return Result(ResultType.FAILED, str(e))

        retry_with_feature_key = False
        action_params = {"name": self.cert_name}
        LOG.debug(f"Running action {action_cmd} with params {action_params}")
        try:
            action_result = self.jhelper.run_action(
                unit, self.model, action_cmd, action_params
            )
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} on {unit} failed: {str(e)}")
            retry_with_feature_key = True

        # For backward compatiblity reasons, try to run remove-ca-cert action
        # with feature_key as ca cert name
        if retry_with_feature_key:
            action_params = {"name": self.feature_key}
            LOG.debug(f"Running action {action_cmd} with params {action_params}")
            try:
                action_result = self.jhelper.run_action(
                    unit, self.model, action_cmd, action_params
                )
            except ActionFailedException as e:
                LOG.debug(f"Running action {action_cmd} on {unit} failed")
                return Result(ResultType.FAILED, str(e))

        LOG.debug(f"Result from action {action_cmd}: {action_result}")

        return Result(ResultType.COMPLETED)


def certificate_questions(app: str, unit: str | None, subject: str):
    # For backward compatibility, if unit is provided, ask question
    # with unit name else with app name
    if unit:
        return {
            "certificate": questions.PromptQuestion(
                f"Base64 encoded Certificate for unit {unit} CSR Unique ID: {subject}",
            ),
        }
    else:
        return {
            "certificate": questions.PromptQuestion(
                f"Base64 encoded Certificate for app {app} CSR Unique ID: {subject}",
            ),
        }


def get_outstanding_certificate_requests(
    app: str, model: str, jhelper: JujuHelper
) -> dict:
    """Get outstanding certificate requests from manual-tls-certificate operator.

    Returns the result from the action get-outstanding-certificate-requests.
    Raises LeaderNotFoundException, ActionFailedException.
    """
    action_cmd = "get-outstanding-certificate-requests"
    unit = jhelper.get_leader_unit(app, model)
    action_result = jhelper.run_action(unit, model, action_cmd)
    return action_result


def handle_list_outstanding_csrs(
    ca_provider_app: str, interface: str, model: str, deployment: Deployment
) -> list[dict[str, str | None]]:
    r"""List outstanding CSRs.

    Output will be in format:
    [
        {"app_name": "traefik",
        "unit_name": None,
        "relation_id": 3,
        "csr": "-----BEGIN CERTIFICATE REQUEST-----\nMIIC..."},
        # Backward compatible example without relation_id
        {"app_name": "traefik-public",
        "unit_name": "traefik-public/0",
        "relation_id": None,
        "csr": "-----BEGIN CERTIFICATE REQUEST-----\nMIIC..."},
    ]
    """
    action_cmd = "get-outstanding-certificate-requests"
    jhelper = JujuHelper(deployment.juju_controller)
    try:
        action_result = get_outstanding_certificate_requests(
            ca_provider_app, model, jhelper
        )
    except LeaderNotFoundException as e:
        LOG.debug(f"Unable to get {ca_provider_app} leader to print CSRs")
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
    if certs_to_process == []:
        LOG.debug("No outstanding CSRs to list")
        return []

    csrs: list[dict[str, str | None]] = []
    relation_map: dict[str, str] = {}
    is_relation_id_present = certs_to_process[0].get("relation_id") is not None
    if is_relation_id_present:
        LOG.debug("Using relation_id to map CSRs")
        try:
            relation_map = jhelper.get_relation_map(ca_provider_app, interface, model)
        except JujuException as e:
            LOG.debug("Unable to get relation map")
            raise click.ClickException("Unable to get relation map") from e

    processed_records = set()
    for record in certs_to_process:
        # Avoid processing duplicate records
        # This can happen if multiple units from same application
        # request certificates with same CSR and the relation_id
        # will be same for those units.
        hashable_record = tuple(sorted(record.items()))
        if hashable_record in processed_records:
            continue

        processed_records.add(hashable_record)

        relation_id = record.get("relation_id")
        if relation_id:
            record["relation_id"] = str(relation_id)
            record["app_name"] = relation_map.get(f"{interface}:{relation_id}")
            record["unit_name"] = None
        else:
            # For backward compatibility with older versions of
            # manual-tls-certificates which do not return relation_id
            record["app_name"] = record.get("unit_name").split("/")[0]
            record["relation_id"] = None

        csrs.append(record)

    return csrs


class ConfigureTLSCertificatesStep(BaseStep):
    """Configure TLS certificates.

    Common for both TLS CA and TLS Vault features.
    TLS CA configures certificates for traefik. traefik-public,
    traefik-rgw units and TLS Vault configures certificates for
    vault units.
    """

    _CONFIG = "FeatureCACertificatesConfig"

    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        ca_cert: str,
        ca_chain: str | None = None,
        deployment_preseed: dict | None = None,
    ):
        super().__init__("Configure CA certs", "Configuring CA certificates")
        self.client = client
        self.jhelper = jhelper
        self.ca_cert = ca_cert
        self.ca_chain = ca_chain
        self.preseed = deployment_preseed or {}
        self.app = CA_MANUAL_TLS_CERTIFICATE
        self.interface = CA_MANUAL_TLS_CERTIFICATE_INTERFACE
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
            self.app, self.model, self.jhelper
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

        try:
            relation_map = self.jhelper.get_relation_map(
                self.app, self.interface, self.model
            )
        except JujuException as e:
            LOG.debug("Unable to get relation map")
            raise click.ClickException("Unable to get relation map") from e

        processed_records = set()
        for record in certs_to_process:
            # Avoid processing duplicate records
            # This can happen if multiple units from same application
            # request certificates with same CSR and the relation_id
            # will be same for those units.
            hashable_record = tuple(sorted(record.items()))
            if hashable_record in processed_records:
                continue

            processed_records.add(hashable_record)

            # In case of manual-tls-certificates 1/stable, unit_name is not provided
            unit_name = record.get("unit_name")
            csr = record.get("csr")
            app = record.get("application_name")
            relation_id = record.get("relation_id")
            # In case of manual-tls-certificates 1/stable, get app name from relation_id
            # fall back to getting application name from unit_name
            if relation_id:
                app = relation_map.get(f"{self.interface}:{relation_id}")
            elif unit_name:
                app = unit_name.split("/")[0]

            if not app:
                raise click.ClickException(
                    f"Could not map an application for {relation_id} {unit_name}"
                )

            # Each unit can have multiple CSRs
            subject = get_subject_from_csr(csr)
            if not subject:
                raise click.ClickException(f"Not a valid CSR for unit {unit_name}")

            cert_questions = certificate_questions(app, unit_name, subject)
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
            unit = self.jhelper.get_leader_unit(self.app, self.model)
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
                "relation-id": request.get("relation_id"),
                "certificate": request.get("certificate"),
                "ca-certificate": self.ca_cert,
                "certificate-signing-request": str(csr),
            }

            # 1. If user does not provide ca_chain, do not send the parameter
            # This may happen in self-signed CA scenario
            # manual-tls-certificates will handle the missing ca-chain parameter
            # by appending the certificate and ca-certificate as the chain in that order
            # 2. If user provides ca_chain, send [certificate, ca_certificate, ca_chain]
            # as ca-chain parameter
            # Note: All the certificates are in base64 encoded format
            if self.ca_chain:
                try:
                    action_params["ca-chain"] = generate_ca_chain(
                        request.get("certificate"), self.ca_cert, self.ca_chain
                    )
                except binascii.Error as e:
                    LOG.debug(f"Unable to encode CA chain: {str(e)}")
                    return Result(ResultType.FAILED, "Unable to encode CA chain")

            LOG.debug(f"Running action {action_cmd} with params {action_params}")
            try:
                action_result = self.jhelper.run_action(
                    unit, self.model, action_cmd, action_params
                )
            except ActionFailedException as e:
                LOG.debug(f"Running action {action_cmd} on {unit} failed")
                return Result(ResultType.FAILED, str(e))

            LOG.debug(f"Result from action {action_cmd}: {action_result}")
            if action_result.get("return-code", 0) > 1:
                return Result(
                    ResultType.FAILED, f"Action {action_cmd} on {unit} returned error"
                )

        return Result(ResultType.COMPLETED)
