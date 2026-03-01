# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import logging

import petname  # type: ignore [import-untyped]
import pydantic
import snaphelpers
from rich.console import Console

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
    URLNotFoundException,
)
from sunbeam.commands.proxy import proxy_questions
from sunbeam.core.checks import DaemonGroupCheck
from sunbeam.core.common import SunbeamException
from sunbeam.core.deployment import PROXY_CONFIG_KEY, CertPair, Deployment, Networks
from sunbeam.core.juju import (
    CONTROLLER,
    JujuAccount,
    JujuAccountNotFound,
    JujuController,
)
from sunbeam.core.k8s import K8SHelper
from sunbeam.core.openstack import (
    ENDPOINTS_CONFIG_KEY,
    REGION_CONFIG_KEY,
    generate_endpoint_preseed_questions,
)
from sunbeam.core.questions import QuestionBank, load_answers, show_questions
from sunbeam.features.microceph.steps import CONFIG_DISKS_KEY, microceph_questions
from sunbeam.provider.local.steps import local_external_network_agent_questions
from sunbeam.steps.clusterd import (
    BOOTSTRAP_CONFIG_KEY,
    CLUSTERD_PORT,
    bootstrap_questions,
)
from sunbeam.steps.configure import (
    CLOUD_CONFIG_SECTION,
    ext_net_questions,
    ext_net_questions_local_only,
    user_questions,
)
from sunbeam.steps.k8s import K8S_ADDONS_CONFIG_KEY, k8s_addons_questions
from sunbeam.steps.openstack import (
    TOPOLOGY_KEY,
    database_topology_questions,
    endpoint_questions,
    region_questions,
)

LOG = logging.getLogger(__name__)
LOCAL_TYPE = "local"


class LocalDeployment(Deployment):
    name: str = petname.generate()  # type: ignore
    url: str = "local"
    type: str = LOCAL_TYPE
    _client: Client | None = pydantic.PrivateAttr(default=None)
    _management_cidr: str | None = pydantic.PrivateAttr(default=None)

    def __init__(self, **data):
        super().__init__(**data)
        if self.juju_account is None:
            self.juju_account = self._load_juju_account()
        if self.juju_controller is None:
            self.juju_controller = self._load_juju_controller()
        if self.clusterd_certpair is None:
            self.clusterd_certpair = self._load_cert_pair()

    def _load_juju_account(self) -> JujuAccount | None:
        try:
            juju_account = JujuAccount.load(snaphelpers.Snap().paths.user_data)
            LOG.debug(f"Local account found: {juju_account.user}")
            return juju_account
        except JujuAccountNotFound:
            LOG.debug("No juju account found", exc_info=True)
            return None

    def _load_juju_controller(self) -> JujuController | None:
        try:
            return JujuController.load(self.get_client())
        except URLNotFoundException:
            LOG.debug("Url not found, is microcluster bootstrapped?", exc_info=True)
            return None
        except ConfigItemNotFoundException:
            LOG.debug("No juju controller found", exc_info=True)
            return None
        except ClusterServiceUnavailableException:
            LOG.debug("Clusterd service unavailable", exc_info=True)
            return None
        except SunbeamException:
            LOG.debug("Failed to load juju controller", exc_info=True)
            return None

    def _load_cert_pair(self) -> CertPair | None:
        try:
            return CertPair(**self.get_client().cluster.get_server_certpair())
        except URLNotFoundException:
            LOG.debug("Url not found, is microcluster bootstrapped?", exc_info=True)
            return None
        except ClusterServiceUnavailableException:
            LOG.debug("Clusterd service unavailable", exc_info=True)
            return None
        except SunbeamException:
            LOG.debug("Failed to load cert pair", exc_info=True)
            return None

    def reload_credentials(self):
        """Refresh instance juju credentials."""
        self.juju_account = self._load_juju_account()
        self.juju_controller = self._load_juju_controller()
        self.clusterd_certpair = self._load_cert_pair()

    @property
    def openstack_machines_model(self) -> str:
        """Return the openstack machines model name."""
        return "openstack-machines"

    @property
    def controller(self) -> str:
        """Return the controller name."""
        if self.juju_controller and self.juju_controller.is_external:
            return self.juju_controller.name

        # Juju controller not yet set, return defaults
        return CONTROLLER

    def get_client(self) -> Client:
        """Return a client for the deployment."""
        if self._client is None:
            check = DaemonGroupCheck()
            if not check.run():
                raise SunbeamException(check.message)
            self._client = Client.from_socket()
        return self._client

    def get_management_cidr(self) -> str:
        """Return the management CIDR."""
        if self._management_cidr is not None:
            return self._management_cidr
        bootstrap_config = load_answers(self.get_client(), BOOTSTRAP_CONFIG_KEY)
        management_cidr = bootstrap_config.get("bootstrap", {}).get("management_cidr")
        if management_cidr is None:
            raise ValueError("Management CIDR not found in bootstrap config")
        self._management_cidr = management_cidr
        return management_cidr

    def get_clusterd_http_address(self) -> str:
        """Return the address of the clusterd server."""
        local_ip = utils.get_local_ip_by_cidr(self.get_management_cidr())
        if ipaddress.ip_address(local_ip).version == 6:
            local_ip = f"[{local_ip}]"
        address = f"https://{local_ip}:{CLUSTERD_PORT}"
        return address

    def generate_core_config(self, console: Console) -> str:
        """Generate preseed for deployment."""
        try:
            management_cidr = self.get_management_cidr()
        except (ValueError, ClusterServiceUnavailableException):
            management_cidr = None
        fqdn = utils.get_fqdn(management_cidr)
        client = self.get_client()
        preseed_content = ["core:", "  config:"]
        try:
            variables = load_answers(client, PROXY_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            default_proxy_settings = self.get_default_proxy_settings()
            default_proxy_settings = {
                k.lower(): v for k, v in default_proxy_settings.items() if v
            }
            variables = {"proxy": {}}

            variables["proxy"]["proxy_required"] = (
                True if default_proxy_settings else False
            )
            variables["proxy"].update(default_proxy_settings)
        proxy_bank = QuestionBank(
            questions=proxy_questions(),
            console=console,
            previous_answers=variables.get("proxy", {}),
        )
        preseed_content.extend(show_questions(proxy_bank, section="proxy"))

        variables = {}
        try:
            if client is not None:
                variables = load_answers(client, TOPOLOGY_KEY)
        except ClusterServiceUnavailableException:
            pass
        database_bank = QuestionBank(
            questions=database_topology_questions(),
            console=console,
            previous_answers=variables,
        )
        preseed_content.extend(show_questions(database_bank))

        variables = {}
        try:
            if client is not None:
                variables = load_answers(client, REGION_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            pass
        region_bank = QuestionBank(
            questions=region_questions(),
            console=console,
            previous_answers=variables,
        )
        preseed_content.extend(show_questions(region_bank))

        variables = {}
        try:
            variables = load_answers(client, BOOTSTRAP_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            variables = {}
        bootstrap_bank = QuestionBank(
            questions=bootstrap_questions(),
            console=console,
            previous_answers=variables.get("bootstrap", {}),
        )
        preseed_content.extend(show_questions(bootstrap_bank, section="bootstrap"))

        try:
            variables = load_answers(client, K8S_ADDONS_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            variables = {}
        k8s_addons_bank = QuestionBank(
            questions=k8s_addons_questions(),
            console=console,
            previous_answers=variables.get("k8s-addons", {}),
        )
        preseed_content.extend(show_questions(k8s_addons_bank, section="k8s-addons"))

        try:
            variables = load_answers(client, CLOUD_CONFIG_SECTION)
        except ClusterServiceUnavailableException:
            variables = {}
        user_bank = QuestionBank(
            questions=user_questions(),
            console=console,
            previous_answers=variables.get("user"),
        )
        preseed_content.extend(show_questions(user_bank, section="user"))
        ext_net_bank_local = QuestionBank(
            questions=ext_net_questions_local_only(),
            console=console,
            previous_answers=variables.get("external_network"),
        )
        preseed_content.extend(
            show_questions(
                ext_net_bank_local,
                section="external_network",
                section_description="Local Access",
            )
        )
        ext_net_bank_remote = QuestionBank(
            questions=ext_net_questions() | local_external_network_agent_questions(),
            console=console,
            previous_answers=variables.get("external_network"),
        )
        preseed_content.extend(
            show_questions(
                ext_net_bank_remote,
                section="external_network",
                section_description="Remote Access",
                comment_out=True,
            )
        )

        # Endpoints configuration
        try:
            variables = load_answers(client, ENDPOINTS_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            variables = {}

        preseed_content.extend(
            generate_endpoint_preseed_questions(endpoint_questions, console, variables)
        )

        try:
            variables = load_answers(client, CONFIG_DISKS_KEY)
        except ClusterServiceUnavailableException:
            variables = {}
        microceph_content: list[str] = []
        for name, disks in variables.get("microceph_config", {fqdn: None}).items():
            microceph_config_bank = QuestionBank(
                questions=microceph_questions(),
                console=console,
                previous_answers=disks,
            )
            lines = show_questions(
                microceph_config_bank,
                section="microceph_config",
                subsection=name,
                section_description="MicroCeph config",
            )
            # if there's more than one microceph,
            # don't rewrite the section and section description
            if len(microceph_content) < 2:
                microceph_content.extend(lines)
            else:
                microceph_content.extend(lines[2:])
        preseed_content.extend(microceph_content)

        preseed_content_final = "\n".join(preseed_content)
        return preseed_content_final

    def get_default_proxy_settings(self) -> dict:
        """Return default proxy settings."""
        with open("/etc/environment", mode="r", encoding="utf-8") as file:
            current_env = dict(
                line.strip().split("=", 1) for line in file if "=" in line
            )

        proxy_configs = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]
        proxy = {p: v.strip("\"'") for p in proxy_configs if (v := current_env.get(p))}
        return proxy

    def get_space(self, network: Networks) -> str:
        """Get space associated to network.

        Local deployment only supports management space as of now.
        """
        return "management"

    @property
    def internal_ip_pool(self) -> str:
        """Name of the internal IP pool."""
        return K8SHelper().get_internal_pool_name()
