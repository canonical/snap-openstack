# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
import pydantic
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.service import ConfigItemNotFoundException
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
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import FeatureConfig
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.v1.base import BaseFeatureGroup
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    WaitForApplicationsStep,
)
from sunbeam.utils import pass_method_obj

CERTIFICATE_FEATURE_KEY = "TlsProvider"
# Time out for keystone to settle once ingress change relation data
INGRESS_CHANGE_APPLICATION_TIMEOUT = 1200
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

    @property
    def ca_cert_name(self) -> str:
        """CA Cert name to be used to add to keystone."""
        # Keystone lists ca cert names with any .'s replaced
        # with -.
        # https://opendev.org/openstack/sunbeam-charms/src/commit/c8761241f3b7be381101fbe5942aa2174daf1797/charms/keystone-k8s/src/charm.py#L629
        return self.feature_key.replace(".", "-")

    @click.group()
    def enable_tls(self) -> None:
        """Enable TLS group."""

    @click.group()
    def disable_tls(self) -> None:
        """Disable TLS group."""

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
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            AddCACertsToKeystoneStep(
                jhelper,
                self.ca_cert_name,
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

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks after the feature is disabled."""
        super().post_disable(deployment, show_hints)

        client = deployment.get_client()
        jhelper = JujuHelper(deployment.juju_controller)

        model = OPENSTACK_MODEL
        apps_to_monitor = ["traefik", "traefik-public", "keystone"]
        if client.cluster.list_nodes_by_role("storage"):
            apps_to_monitor.append("traefik-rgw")

        plan = [
            RemoveCACertsFromKeystoneStep(jhelper, self.ca_cert_name, self.feature_key),
            WaitForApplicationsStep(
                jhelper, apps_to_monitor, model, INGRESS_CHANGE_APPLICATION_TIMEOUT
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
        ca_chain: str,
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
            "chain": self.ca_chain,
        }

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
