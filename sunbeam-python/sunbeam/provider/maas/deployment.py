# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import enum
from typing import TYPE_CHECKING, Type, TypeGuard

import pydantic

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.proxy import proxy_questions
from sunbeam.core.deployment import PROXY_CONFIG_KEY, Deployment, Networks
from sunbeam.core.k8s import K8SHelper
from sunbeam.core.openstack import (
    ENDPOINTS_CONFIG_KEY,
    REGION_CONFIG_KEY,
    generate_endpoint_preseed_questions,
)
from sunbeam.core.questions import Question, QuestionBank, load_answers, show_questions
from sunbeam.steps.configure import (
    CLOUD_CONFIG_SECTION,
    ext_net_questions,
    user_questions,
)
from sunbeam.steps.openstack import (
    TOPOLOGY_KEY,
    database_topology_questions,
    endpoint_questions,
    region_questions,
)

if TYPE_CHECKING:
    from sunbeam.provider.maas.client import MaasClient

MAAS_TYPE = "maas"


class RoleTags(enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    JUJU_CONTROLLER = "juju-controller"
    SUNBEAM = "sunbeam"
    REGION_CONTROLLER = "region_controller"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


ROLE_NETWORK_MAPPING = {
    RoleTags.CONTROL: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.PUBLIC,
        Networks.STORAGE,
    ],
    RoleTags.REGION_CONTROLLER: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.PUBLIC,
    ],
    RoleTags.COMPUTE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
    ],
    RoleTags.STORAGE: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.STORAGE,
        Networks.STORAGE_CLUSTER,
    ],
    RoleTags.NETWORK: [
        Networks.INTERNAL,
        Networks.MANAGEMENT,
        Networks.PUBLIC,
        Networks.DATA,
        Networks.STORAGE,
    ],
    RoleTags.JUJU_CONTROLLER: [
        Networks.MANAGEMENT,
    ],
    RoleTags.SUNBEAM: [
        Networks.MANAGEMENT,
    ],
}


class StorageTags(enum.Enum):
    CEPH = "ceph"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class NicTags(enum.Enum):
    COMPUTE = "neutron:physnet1"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasDeployment(Deployment):
    name: str = pydantic.Field(pattern=r"^[a-zA-Z0-9-]+$", max_length=246)
    type: str = MAAS_TYPE
    token: str
    network_mapping: dict[str, str | None] = {}
    clusterd_address: str | None = None
    clusterd_certificate_authority: str | None = None
    _client: Client | None = pydantic.PrivateAttr(default=None)

    @property
    def controller(self) -> str:
        """Return controller name."""
        if self.juju_controller:
            return self.juju_controller.name

        # Juju controller not yet set, return defaults
        return self.name + "-controller"

    @property
    def resource_tag(self) -> str:
        """Return resource tag."""
        return "openstack-" + self.name

    @property
    def public_api_label(self) -> str:
        """Return public API label."""
        return self.name + "-public-api"

    @property
    def internal_api_label(self) -> str:
        """Return internal API label."""
        return self.name + "-internal-api"

    @pydantic.validator("type")
    def type_validator(cls, v: str, values: dict) -> str:  # noqa N805
        if v != MAAS_TYPE:
            raise ValueError("Deployment type must be MAAS.")
        return v

    @classmethod
    def import_step(cls) -> Type:
        """Return a step for importing a deployment.

        This step will be used to make sure the deployment is valid.
        The step must take as constructor arguments: DeploymentsConfig, Deployment.
        The Deployment must be of the type that the step is registered for.
        """
        from sunbeam.provider.maas.commands import AddMaasDeployment

        return AddMaasDeployment

    @property
    def openstack_machines_model(self) -> str:
        """Return the openstack machines model name."""
        return "openstack-machines"

    @property
    def infra_model(self) -> str:
        """Return the openstack infra model name."""
        return "openstack-infra"

    def get_client(self) -> Client:
        """Return a client for the deployment."""
        if self.clusterd_address is None:
            raise ValueError("Clusterd address not set.")
        if self.clusterd_certificate_authority is None:
            raise ValueError("Clusterd certificate authority not set.")
        if self.clusterd_certpair is None:
            raise ValueError("Clusterd certificate not set.")
        if self._client is None:
            certificate_authority = self.clusterd_certificate_authority
            certificate = self.clusterd_certpair.certificate
            private_key = self.clusterd_certpair.private_key
            self._client = Client.from_http(
                self.clusterd_address, certificate_authority, certificate, private_key
            )
        return self._client

    def get_clusterd_http_address(self) -> str:
        """Return the address of the clusterd server."""
        if self.clusterd_address is None:
            raise ValueError("Clusterd address not set.")
        return self.clusterd_address

    def generate_core_config(self, console) -> str:
        """Generate preseed for deployment."""
        try:
            client = self.get_client()
        except ValueError:
            client = None

        # to avoid circular import
        from sunbeam.provider.maas.client import MaasClient

        maas_client = MaasClient.from_deployment(self)

        preseed_content = ["core:", "  config:"]
        variables = {}
        try:
            if client is not None:
                variables = load_answers(client, PROXY_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            pass

        if not variables:
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
            if client is not None:
                variables = load_answers(client, CLOUD_CONFIG_SECTION)
        except ClusterServiceUnavailableException:
            pass
        user_bank = QuestionBank(
            questions=maas_user_questions(maas_client),
            console=console,
            previous_answers=variables.get("user"),
        )
        preseed_content.extend(show_questions(user_bank, section="user"))
        ext_net_bank_remote = QuestionBank(
            questions=ext_net_questions(),
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

        variables = {}
        try:
            if client is not None:
                variables = load_answers(client, ENDPOINTS_CONFIG_KEY)
        except ClusterServiceUnavailableException:
            pass

        preseed_content.extend(
            generate_endpoint_preseed_questions(endpoint_questions, console, variables)
        )

        preseed_content.extend(
            self.get_feature_manager().get_preseed_questions_content()
        )

        preseed_content_final = "\n".join(preseed_content)
        return preseed_content_final

    def get_default_proxy_settings(self) -> dict:
        """Return default proxy settings."""
        # to avoid circular import
        from sunbeam.provider.maas.client import MaasClient

        maas_client = MaasClient.from_deployment(self)
        proxy = maas_client.get_http_proxy()
        if proxy is None:
            return {}

        subnets = maas_client.get_subnets()
        subnets_cidr = (subnet["cidr"] for subnet in subnets if subnet.get("cidr"))
        no_proxy = ",".join(subnets_cidr)
        return {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "NO_PROXY": no_proxy}

    def get_space(self, network: Networks) -> str:
        """Return space by name."""
        space = self.network_mapping.get(network.value)
        if space is None:
            raise ValueError(f"Space for network {network.value} not set.")
        return space

    @property
    def internal_ip_pool(self) -> str:
        """Name of the internal IP pool."""
        return K8SHelper().get_internal_pool_name()

    @property
    def public_ip_pool(self) -> str:
        """Name of the public IP pool."""
        return self.public_api_label


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return isinstance(deployment, MaasDeployment)


def maas_user_questions(
    maas_client: "MaasClient",
) -> dict[str, Question]:
    questions = user_questions()
    questions["nameservers"].default_function = lambda: " ".join(
        maas_client.get_dns_servers()
    )
    # On MAAS, access is always remote
    questions.pop("remote_access_location", None)
    return questions
