# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import binascii
import json
import logging
import queue
from pathlib import Path
from textwrap import dedent

import click
import pydantic
import yaml
from packaging.version import Version
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core import questions
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    Result,
    ResultType,
    StepContext,
    delete_config,
    read_config,
    run_plan,
    str_presenter,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    ApplicationStatusOverlay,
    ExecFailedException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.k8s import K8S_APP_NAME
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.feature_gates import feature_gate_command, is_feature_gate_enabled
from sunbeam.features.interface.utils import (
    encode_base64_as_string,
    generate_ca_chain,
    get_subject_from_csr,
    is_ca_certificate,
    is_certificate_valid,
)
from sunbeam.features.interface.v1.base import FeatureRequirement
from sunbeam.features.interface.v1.openstack import (
    DisableOpenStackApplicationStep,
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.features.tls.common import (
    CA_MANUAL_TLS_CERTIFICATE,
    CA_MANUAL_TLS_CERTIFICATE_INTERFACE,
    get_outstanding_certificate_requests,
    handle_list_outstanding_csrs,
)
from sunbeam.steps.k8s import (
    K8S_CONFIG_KEY,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import (
    MANUAL_TLS_CERTIFICATES_CHANNEL,
    MULTUS_CHANNEL,
    OPENSTACK_CHANNEL,
)

LOG = logging.getLogger(__name__)
console = Console()

# Terraform plan names and directories for CNI infra/setup
LOADBALANCER_CNI_TFPLAN = "loadbalancer-plan"
LOADBALANCER_CNI_TFPLAN_DIR = "deploy-cni"
LOADBALANCER_SETUP_TFPLAN = "loadbalancer-setup-plan"
LOADBALANCER_SETUP_TFPLAN_DIR = "deploy-loadbalancer-setup"
LOADBALANCER_SETUP_CONFIG_KEY = "TerraformVarsFeatureLoadbalancerSetupPlan"
LOADBALANCER_CNI_CONFIG_KEY = "TerraformVarsFeatureLoadbalancerCNIPlan"
LOADBALANCER_CNI_DEPLOY_TIMEOUT = 900  # 15 minutes

# Config section key for persisting Amphora resource config via questions.write_answers
AMPHORA_CONFIG_SECTION = "LoadbalancerAmphoraConfig"

# Amphora management network attachment definition name (used in octavia config)
AMPHORA_NETWORK_ATTACHMENT_NAME = "octavia-mgmt-net"

# Question field keys — top-level toggle
_AMPHORA_ENABLED_KEY = "amphora_enabled"

# Question field keys — resource IDs
_AMP_IMAGE_TAG_KEY = "amp_image_tag"
_AUTOCREATE_IMAGE_KEY = "autocreate_image"
_AUTOCREATE_FLAVOR_KEY = "autocreate_flavor"
_FLAVOR_KEY = "amp_flavor_id"
_AUTOCREATE_NETWORK_KEY = "autocreate_network"
_AUTOCREATE_SECGROUPS_KEY = "autocreate_securitygroups"
_NETWORK_KEY = "lb_mgmt_network_id"
_NETWORK_CIDR_KEY = "lb_mgmt_cidr"
_SUBNET_KEY = "lb_mgmt_subnet_id"
_SECGROUPS_KEY = "lb_mgmt_secgroup_ids"
_HEALTH_SECGROUP_KEY = "lb_health_secgroup_id"

# Config key for storing certificate answers for Octavia CSRs
AMPHORA_CERTIFICATES_CONFIG = "OctaviaCertificatesConfig"

# Octavia workload status messages used as wait conditions
OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE = (
    "(amphora-network) Amphora management network interface not detected"
)
# Blocked messages emitted by the octavia charm's amphora compound-status slot
# (the charm prefixes these with "(amphora) " when surfaced as the app status).
OCTAVIA_AMPHORA_RELATIONS_MISSING_MESSAGE = (
    "(amphora) amphora-issuing-ca and amphora-controller-cert "
    "relations required for Amphora"
)
OCTAVIA_AMPHORA_CA_CERT_MESSAGE = (
    "(amphora) Amphora issuing CA certificate not yet provided "
    "by amphora-issuing-ca integration"
)
OCTAVIA_AMPHORA_CONTROLLER_CERT_MESSAGE = (
    "(amphora) Amphora controller certificate not yet provided "
    "by amphora-controller-cert integration"
)

# Cilium annotation to enable non-exclusive CNI mode (allows Multus)
CILIUM_EXCLUSIVE_ANNOTATION = "k8sd/v1alpha1/cilium/exclusive=false"

# OVS bridge and socket for Multus NetworkAttachmentDefinition
OVS_BRIDGE = "br-int"
MICROOVN_OVS_SOCKET = "unix:/var/snap/microovn/common/run/switch/db.sock"


def _csr_common_name(csr_pem: str) -> str:
    """Return the Common Name from a PEM CSR, or empty string on failure."""
    try:
        from cryptography import x509

        csr = x509.load_pem_x509_csr(csr_pem.encode())
        attrs = csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        return str(attrs[0].value) if attrs else ""
    except Exception:
        return ""


def _build_nad_yaml(
    network_attachment_name: str,
    network_id: str,
    subnet_id: str,
    secgroup_ids: list[str],
) -> str:
    """Build a Multus NetworkAttachmentDefinition YAML for the lb-mgmt network."""
    ovs_cni_resource = f"ovs-cni.network.kubevirt.io/{OVS_BRIDGE}"
    secgroup_list = ",".join(sg for sg in secgroup_ids if sg)
    return dedent(f"""\
        apiVersion: "k8s.cni.cncf.io/v1"
        kind: NetworkAttachmentDefinition
        metadata:
          name: {network_attachment_name}
          namespace: {OPENSTACK_MODEL}
          annotations:
            k8s.v1.cni.cncf.io/resourceName: {ovs_cni_resource}
        spec:
          config: |
            {{
              "cniVersion": "0.4.0",
              "type": "openstack-port-cni",
              "bridge": "{OVS_BRIDGE}",
              "socket_file": "{MICROOVN_OVS_SOCKET}",
              "subnet_id": "{subnet_id}",
              "network_id": "{network_id}",
              "security_group_ids": "{secgroup_list}",
              "delegate_plugin": "ovs"
            }}
    """)


class _CertificateEntry(pydantic.BaseModel):
    """A single signed certificate plus its CA material, keyed by CSR subject."""

    certificate: str = ""
    ca_certificate: str = ""
    ca_chain: str = ""


class LoadbalancerFeatureConfig(FeatureConfig):
    """Manifest config for the loadbalancer feature (Amphora provider).

    Exposed under ``features.loadbalancer.config`` in the sunbeam manifest.

    Leave any resource field empty (the default) to have Terraform create it
    automatically.  Set ``autocreate_image: true`` to also have Terraform
    download and upload the Amphora image.

    Certificates can be pre-seeded under ``certificates`` keyed by the CSR
    x500UniqueIdentifier subject reported by ``list-outstanding-csrs``.
    """

    amphora_enabled: bool = True
    amp_image_tag: str = "octavia-amphora"
    autocreate_image: bool = False
    autocreate_flavor: bool = True
    amp_flavor_id: str = ""
    autocreate_network: bool = True
    lb_mgmt_cidr: str = "fd00:a9fe:a9fe::/64"  # IPv4 alternative: "172.31.0.0/24"
    lb_mgmt_network_id: str = ""
    lb_mgmt_subnet_id: str = ""
    autocreate_securitygroups: bool = True
    lb_mgmt_secgroup_ids: list[str] = pydantic.Field(default_factory=list)
    lb_health_secgroup_id: str = ""
    certificates: dict[str, _CertificateEntry] = pydantic.Field(default_factory=dict)


class LoadbalancerAmphoraConfig(pydantic.BaseModel):
    """Internal data model holding resolved Amphora configuration.

    Populated from the ``AMPHORA_CONFIG_SECTION`` clusterd key which stores
    both resource IDs (written by AmphoraConfigStep / CreateAmphoraResourcesStep)
    and certificate content (written by AmphoraConfigStep).
    """

    model_config = pydantic.ConfigDict(extra="ignore")

    amphora_enabled: bool = True
    amp_image_tag: str = "octavia-amphora"
    autocreate_image: bool = False
    autocreate_flavor: bool = True
    amp_flavor_id: str = ""
    autocreate_network: bool = True
    lb_mgmt_cidr: str = "fd00:a9fe:a9fe::/64"  # IPv4 alternative: "172.31.0.0/24"
    lb_mgmt_network_id: str = ""
    lb_mgmt_subnet_id: str = ""
    autocreate_securitygroups: bool = True
    lb_mgmt_secgroup_ids: list[str] = pydantic.Field(default_factory=list)
    lb_health_secgroup_id: str = ""


def _amphora_questions() -> dict[str, questions.Question]:
    """Return QuestionBank questions dict for Amphora configuration.

    Single source of truth used by both the interactive CLI (``AmphoraConfigStep``)
    and ``preseed_questions_content`` (``manifest generate``).

    For resource ID fields, leaving the answer empty instructs Terraform to
    create that resource automatically.
    """
    return {
        _AMPHORA_ENABLED_KEY: questions.ConfirmQuestion(
            "Enable Octavia Amphora provider?",
            default_value=True,
            description=(
                "Enables the Amphora VM-based load-balancer backend for Octavia."
                " Requires the microovn-sdn and loadbalancer-amphora feature gates."
            ),
        ),
        _AMP_IMAGE_TAG_KEY: questions.PromptQuestion(
            "Amphora image tag",
            default_value="octavia-amphora",
            description=(
                "Glance tag used by Octavia to locate the Amphora VM image."
                " An image with this tag must exist in Glance before Octavia can"
                " create load-balancer instances. Use 'auto-create image' below"
                " to have Sunbeam upload one automatically."
            ),
        ),
        _AUTOCREATE_IMAGE_KEY: questions.ConfirmQuestion(
            "Auto-create Amphora image?",
            default_value=False,
            description=(
                "If enabled, Sunbeam will download the upstream Octavia Amphora"
                " image from tarballs.opendev.org"
                " (test-only-amphora-x64-haproxy-ubuntu-noble.qcow2)"
                " and upload it to Glance with the tag specified above."
                " Skip this if you already have a suitable image in Glance."
            ),
        ),
        _AUTOCREATE_FLAVOR_KEY: questions.ConfirmQuestion(
            "Auto-create Amphora Nova flavor?",
            default_value=True,
            description=(
                "If enabled, Sunbeam will create a dedicated Nova flavor for"
                " Amphora VM instances automatically. Disable this if you already"
                " have a suitable flavor and want to provide its ID."
            ),
        ),
        _FLAVOR_KEY: questions.PromptQuestion(
            "Amphora Nova flavor ID",
            description=(
                "Nova flavor used when launching Amphora VM instances."
                " Only asked when not auto-creating the flavor."
            ),
        ),
        _AUTOCREATE_NETWORK_KEY: questions.ConfirmQuestion(
            "Auto-create lb-mgmt network and subnet?",
            default_value=True,
            description=(
                "If enabled, Sunbeam will create the Octavia lb-mgmt network and"
                " subnet automatically using an IPv6 ULA subnet"
                " (fd00:a9fe:a9fe::/64). Disable this if you already have a"
                " suitable network and want to provide its IDs."
            ),
        ),
        # lb-mgmt network CIDR: hardcoded as IPv6 ULA in Terraform
        # (fd00:a9fe:a9fe::/64). No prompt needed.
        # Uncomment below to revert to user-configurable IPv4 CIDR.
        # _NETWORK_CIDR_KEY: questions.PromptQuestion(
        #     "lb-mgmt network CIDR",
        #     default_value="172.31.0.0/24",
        #     description=(
        #         "IPv4 CIDR for the lb-mgmt subnet that Sunbeam will create."
        #         " Only used when auto-creating the network."
        #         " Must not overlap with other networks in your environment."
        #     ),
        # ),
        _NETWORK_KEY: questions.PromptQuestion(
            "lb-mgmt network ID",
            description=(
                "Neutron network ID for the Octavia lb-mgmt management network."
                " Only asked when not auto-creating the network."
            ),
        ),
        _SUBNET_KEY: questions.PromptQuestion(
            "lb-mgmt subnet ID",
            description=(
                "Neutron subnet ID within the lb-mgmt network."
                " Only asked when not auto-creating the network."
            ),
        ),
        _AUTOCREATE_SECGROUPS_KEY: questions.ConfirmQuestion(
            "Auto-create Amphora security groups?",
            default_value=True,
            description=(
                "If enabled, Sunbeam will create the Neutron security groups for"
                " Amphora VM ports automatically. Disable this if you already have"
                " suitable security groups and want to provide their IDs."
            ),
        ),
        _SECGROUPS_KEY: questions.PromptQuestion(
            "Security group ID for Amphora VM ports",
            description=(
                "Neutron security group ID to attach to Amphora VM ports"
                " (lb-mgmt-sec-grp). This is passed to the Octavia charm as"
                " amp-secgroup-list. Only asked when not auto-creating security"
                " groups."
            ),
        ),
        _HEALTH_SECGROUP_KEY: questions.PromptQuestion(
            "Security group ID for the Octavia health manager port",
            description=(
                "Neutron security group ID for the Octavia health manager port"
                " (lb-health-mgr-sec-grp). Octavia applies this group to its"
                " health manager port on lb-mgmt-net. Only asked when not"
                " auto-creating security groups."
            ),
        ),
    }


def _amphora_certificate_questions(
    app: str, unit: str | None, subject: str, endpoint: str = "certificates"
) -> dict[str, questions.Question]:
    """Return QuestionBank questions for a single Octavia CSR.

    Extends ``certificate_questions`` with a required CA certificate field and
    an optional CA chain field so the user can supply all signing material in
    one prompt sequence.

    ``endpoint`` is the Juju relation endpoint name on the Octavia side
    (``amphora-issuing-ca`` or ``amphora-controller-cert``) and is embedded
    in the prompt so the user knows which certificate type is expected.
    """
    # Build the prompt label ourselves so we can include the endpoint name.
    if unit:
        cert_prompt = (
            f"Base64 encoded Certificate for unit {unit} "
            f"(endpoint: {endpoint}) CSR Unique ID: {subject}"
        )
    else:
        cert_prompt = (
            f"Base64 encoded Certificate for app {app} "
            f"(endpoint: {endpoint}) CSR Unique ID: {subject}"
        )

    if endpoint == "amphora-issuing-ca":
        cert_description = (
            "This is the Amphora ISSUING CA certificate — it must have "
            "basicConstraints: CA:TRUE. Octavia uses it to sign certificates "
            "for individual Amphora instances."
        )
    else:
        cert_description = (
            "This is the Amphora CONTROLLER certificate (leaf cert) — "
            "it does NOT need to be a CA certificate. Octavia uses it to "
            "authenticate the controller-side of the Amphora TLS connection."
        )

    base: dict[str, questions.Question] = {
        "certificate": questions.PromptQuestion(
            cert_prompt, description=cert_description
        )
    }
    base["ca_certificate"] = questions.PromptQuestion(
        "CA certificate (base64 PEM)",
        description=(
            "The CA certificate that signed the certificate above, base64-encoded."
            " This is passed to Octavia as the issuing CA."
        ),
    )
    base["ca_chain"] = questions.PromptQuestion(
        "CA chain (base64 PEM, optional)",
        description=(
            "Full certificate chain (intermediate + root CAs) base64-encoded."
            " Leave empty if the CA certificate is self-signed or the chain"
            " is not needed."
        ),
    )
    return base


class AmphoraConfigStep(BaseStep):
    """Collect and persist Amphora resource configuration.

    Asks for image tag, autocreate flags, flavor, network, subnet, and
    security groups.  Answers are persisted to clusterd for use by
    ``CreateAmphoraResourcesStep`` and ``UpdateOctaviaAmphoraConfigStep``.

    TLS certificate provisioning is handled separately by
    ``ProvideCertificatesStep`` (``sunbeam loadbalancer provide-certificates``).
    """

    def __init__(
        self,
        deployment: Deployment,
        feature_config: LoadbalancerFeatureConfig | None,
        jhelper: JujuHelper,
        accept_defaults: bool = False,
    ):
        super().__init__(
            "Amphora Configuration",
            "Collecting Amphora resource configuration",
        )
        self.deployment = deployment
        self.feature_config = feature_config
        self.jhelper = jhelper
        self.accept_defaults = accept_defaults

    def has_prompts(self) -> bool:
        """This step interactively prompts the user."""
        return True

    def _get_manifest_preseed(self) -> dict:
        """Extract preseed values from manifest feature config.

        Only fields *explicitly* set in the manifest's ``config:`` section are
        returned as preseed.  When the manifest only contains ``software:``
        (no ``config:`` key), the feature config is still instantiated with
        defaults by the manifest loader — using those defaults would silently
        bypass all interactive prompts.  ``model_fields_set`` tells us which
        fields were actually provided by the user.
        """
        if not self.feature_config:
            return {}
        cfg = self.feature_config
        if not cfg.model_fields_set:
            return {}
        return {k: v for k, v in cfg.model_dump().items() if k in cfg.model_fields_set}

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Interactively collect Amphora resource configuration from the user."""
        # ---------------------------------------------------------------
        # Part 1: resource configuration
        # ---------------------------------------------------------------
        variables = questions.load_answers(
            self.deployment.get_client(), AMPHORA_CONFIG_SECTION
        )
        preseed = self._get_manifest_preseed()

        bank = questions.QuestionBank(
            questions=_amphora_questions(),
            console=console,
            preseed=preseed,
            previous_answers=variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )

        # --- Top-level toggle ---
        amphora_enabled = bank.amphora_enabled.ask()
        variables[_AMPHORA_ENABLED_KEY] = amphora_enabled
        if not amphora_enabled:
            questions.write_answers(
                self.deployment.get_client(), AMPHORA_CONFIG_SECTION, variables
            )
            return

        variables[_AMP_IMAGE_TAG_KEY] = bank.amp_image_tag.ask() or "octavia-amphora"
        variables[_AUTOCREATE_IMAGE_KEY] = bank.autocreate_image.ask()

        # Flavor
        autocreate_flavor = bank.autocreate_flavor.ask()
        variables[_AUTOCREATE_FLAVOR_KEY] = autocreate_flavor
        if autocreate_flavor:
            variables[_FLAVOR_KEY] = ""
        else:
            flavor_id = bank.amp_flavor_id.ask()
            if not flavor_id:
                raise click.ClickException(
                    "Amphora flavor ID is required when automatic "
                    "flavor creation is disabled."
                )
            variables[_FLAVOR_KEY] = flavor_id

        # Network
        autocreate_network = bank.autocreate_network.ask()
        variables[_AUTOCREATE_NETWORK_KEY] = autocreate_network
        if autocreate_network:
            # CIDR is hardcoded as IPv6 in Terraform; no user input needed.
            # IPv4 alternative: uncomment the line below and remove this comment.
            # variables[_NETWORK_CIDR_KEY] = bank.lb_mgmt_cidr.ask() or "172.31.0.0/24"
            variables[_NETWORK_KEY] = ""
            variables[_SUBNET_KEY] = ""
        else:
            network_id = bank.lb_mgmt_network_id.ask()
            subnet_id = bank.lb_mgmt_subnet_id.ask()
            if not network_id or not subnet_id:
                raise click.ClickException(
                    "Both lb_mgmt_network_id and lb_mgmt_subnet_id are required "
                    "when automatic network creation is disabled."
                )
            variables[_NETWORK_KEY] = network_id
            variables[_SUBNET_KEY] = subnet_id
            # IPv4 alternative: uncomment to preserve existing CIDR.
            # variables[_NETWORK_CIDR_KEY] = variables.get(
            #     _NETWORK_CIDR_KEY, "172.31.0.0/24"
            # )

        # Security groups
        autocreate_secgroups = bank.autocreate_securitygroups.ask()
        variables[_AUTOCREATE_SECGROUPS_KEY] = autocreate_secgroups
        if autocreate_secgroups:
            variables[_SECGROUPS_KEY] = []
            variables[_HEALTH_SECGROUP_KEY] = ""
        else:
            mgmt_sg = bank.lb_mgmt_secgroup_ids.ask() or ""
            if isinstance(mgmt_sg, str):
                mgmt_sg = mgmt_sg.strip()
            health_sg = bank.lb_health_secgroup_id.ask() or ""
            if isinstance(health_sg, str):
                health_sg = health_sg.strip()
            if not mgmt_sg or not health_sg:
                raise click.ClickException(
                    "Both the Amphora VM security group ID and the health manager"
                    " security group ID are required when automatic security group"
                    " creation is disabled."
                )
            variables[_SECGROUPS_KEY] = [mgmt_sg]
            variables[_HEALTH_SECGROUP_KEY] = health_sg

        questions.write_answers(
            self.deployment.get_client(), AMPHORA_CONFIG_SECTION, variables
        )

    def run(self, context: StepContext) -> Result:
        """No-op — resource config is persisted during prompt()."""
        return Result(ResultType.COMPLETED)


class ProvideCertificatesStep(BaseStep):
    """Prompt for and provide TLS certificates to Octavia via manual-tls-certificates.

    Fetches outstanding CSRs from the ``manual-tls-certificates`` operator and
    asks the user to supply a signed certificate, CA certificate, and optional
    CA chain for each one.  After providing all certificates, waits for Octavia
    to become active.

    Manifest preseed values under ``features.loadbalancer.config.certificates``
    can be used to bypass interactive prompts.
    """

    def __init__(
        self,
        deployment: Deployment,
        feature_config: LoadbalancerFeatureConfig | None,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Provide Octavia TLS Certificates",
            "Providing TLS certificates to Octavia Amphora",
        )
        self.deployment = deployment
        self.feature_config = feature_config
        self.jhelper = jhelper
        self.process_certs: dict = {}
        self.app = CA_MANUAL_TLS_CERTIFICATE
        self.interface = CA_MANUAL_TLS_CERTIFICATE_INTERFACE
        self.model = OPENSTACK_MODEL

    def has_prompts(self) -> bool:
        """This step interactively prompts the user."""
        return True

    def is_skip(self, context: StepContext) -> Result:
        """Skip if Amphora provider is disabled."""
        cfg = LoadbalancerAmphoraConfig(
            **questions.load_answers(
                self.deployment.get_client(), AMPHORA_CONFIG_SECTION
            )
        )
        if not cfg.amphora_enabled:
            return Result(ResultType.SKIPPED, "Amphora provider disabled")
        return Result(ResultType.COMPLETED)

    def _get_cert_preseed(self) -> dict:
        """Extract certificates preseed from manifest feature config.

        Returns a dict keyed by CSR subject, each value a dict with
        'certificate', 'ca_certificate' and 'ca_chain' keys.  Only used when
        ``certificates`` was explicitly set in the manifest ``config:`` section.
        """
        if not self.feature_config:
            return {}
        cfg = self.feature_config
        if "certificates" not in cfg.model_fields_set:
            return {}
        raw = cfg.certificates
        result = {}
        for subject, entry in raw.items():
            if isinstance(entry, _CertificateEntry):
                result[subject] = entry.model_dump()
            elif isinstance(entry, dict):
                result[subject] = entry
        return result

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Fetch outstanding Octavia CSRs and ask the user to sign each one.

        For each outstanding CSR the user is prompted for:
          - signed certificate (base64 PEM, required)
          - CA certificate     (base64 PEM, required)
          - CA chain           (base64 PEM, optional)

        Manifest preseed values (``features.loadbalancer.config.certificates``)
        are used to bypass interactive prompts when provided.

        Results are stored in ``self.process_certs`` and persisted to
        ``AMPHORA_CERTIFICATES_CONFIG`` so they survive a re-run.
        """
        cert_preseed = self._get_cert_preseed()
        try:
            action_result = get_outstanding_certificate_requests(
                self.app, self.model, self.jhelper
            )
        except (LeaderNotFoundException, ActionFailedException) as e:
            LOG.debug("Could not get outstanding CSRs for Octavia: %s", e)
            raise click.ClickException(
                "Failed to get outstanding Octavia certificate requests."
            ) from e

        if action_result.get("return-code", 0) > 1:
            LOG.debug(
                "get-outstanding-certificate-requests returned error: %s",
                action_result,
            )
            raise click.ClickException(
                "Failed to retrieve outstanding Octavia certificate requests."
            )

        certs_to_process = json.loads(action_result.get("result", "[]"))
        if not certs_to_process:
            LOG.debug("No outstanding CSRs for Octavia; skipping cert prompts")
            return

        cert_variables = questions.load_answers(
            self.deployment.get_client(), AMPHORA_CERTIFICATES_CONFIG
        )
        cert_variables.setdefault("certificates", {})

        try:
            relation_map = self.jhelper.get_relation_map(
                self.app, self.interface, self.model
            )
        except JujuException as e:
            LOG.debug("Unable to get relation map: %s", e)
            raise click.ClickException("Unable to get relation map") from e

        processed_records: set = set()
        for record in certs_to_process:
            hashable_record = tuple(sorted(record.items()))
            if hashable_record in processed_records:
                continue
            processed_records.add(hashable_record)

            unit_name = record.get("unit_name")
            csr = record.get("csr")
            app_name = record.get("application_name")
            relation_id = record.get("relation_id")
            if relation_id:
                record["relation_id"] = str(relation_id)
                app_name = relation_map.get(f"{self.interface}:{relation_id}")
            elif unit_name:
                app_name = unit_name.split("/")[0]

            if not app_name:
                raise click.ClickException(
                    f"Could not map an application for relation_id={relation_id}"
                    f" unit={unit_name}"
                )

            subject = get_subject_from_csr(csr)
            if not subject:
                raise click.ClickException(f"Not a valid CSR for unit {unit_name}")

            is_issuing_ca = "issuing-ca" in _csr_common_name(csr or "").lower()
            endpoint_name = (
                "amphora-issuing-ca" if is_issuing_ca else "amphora-controller-cert"
            )
            cert_bank = questions.QuestionBank(
                questions=_amphora_certificate_questions(
                    app_name, unit_name, subject, endpoint=endpoint_name
                ),
                console=console,
                preseed=cert_preseed.get(subject),
                previous_answers=cert_variables.get("certificates", {}).get(subject),
                show_hint=show_hint,
            )
            cert = cert_bank.certificate.ask()
            if not cert or not is_certificate_valid(cert):
                raise click.ClickException("Not a valid certificate")
            if is_issuing_ca and not is_ca_certificate(cert):
                raise click.ClickException(
                    "The certificate for amphora-issuing-ca must have "
                    "basicConstraints: CA:TRUE — Octavia uses it to sign "
                    "Amphora instance certificates. Please provide a CA certificate."
                )
            ca_cert = cert_bank.ca_certificate.ask() or ""
            if not ca_cert:
                raise click.ClickException(
                    f"CA certificate is required for unit {unit_name}"
                )
            ca_chain = cert_bank.ca_chain.ask() or ""

            self.process_certs[subject] = {
                "app": app_name,
                "unit": unit_name,
                "relation_id": relation_id,
                "csr": csr,
                "certificate": cert,
                "ca_cert": ca_cert,
                "ca_chain": ca_chain,
            }
            cert_variables["certificates"].setdefault(subject, {})
            cert_variables["certificates"][subject]["certificate"] = cert
            cert_variables["certificates"][subject]["ca_certificate"] = ca_cert
            cert_variables["certificates"][subject]["ca_chain"] = ca_chain

        questions.write_answers(
            self.deployment.get_client(), AMPHORA_CERTIFICATES_CONFIG, cert_variables
        )

    def run(self, context: StepContext) -> Result:
        """Apply collected TLS certificates and wait for Octavia to become active."""
        if not self.process_certs:
            # No new CSRs to sign this run — either certs were already provided
            # or a previous run submitted bad certs that the charm accepted.
            # Check whether Octavia is already active; if it's still blocked the
            # user needs to know (e.g. they provided a non-CA cert previously).
            try:
                self.update_status(context, "checking Octavia status")
                self.jhelper.wait_until_active(
                    self.model,
                    apps=["octavia"],
                    timeout=60,
                )
            except (JujuWaitException, TimeoutError):
                return Result(
                    ResultType.FAILED,
                    "No outstanding certificate requests, but Octavia is not yet "
                    "active. The previously provided certificate may be invalid "
                    "(e.g. not a CA certificate). Use "
                    "'sunbeam loadbalancer list-outstanding-csrs' to check "
                    "the current state.",
                )
            return Result(ResultType.COMPLETED)

        try:
            unit = self.jhelper.get_leader_unit(self.app, self.model)
        except LeaderNotFoundException as e:
            LOG.debug("Unable to get %s leader", self.app)
            return Result(ResultType.FAILED, str(e))

        for subject, request in self.process_certs.items():
            csr = encode_base64_as_string(request.get("csr"))
            if not csr:
                return Result(ResultType.FAILED, f"Unable to encode CSR for {subject}")

            action_params: dict = {
                "relation-id": request.get("relation_id"),
                "certificate": request.get("certificate"),
                "certificate-signing-request": str(csr),
            }
            ca_cert = request.get("ca_cert", "")
            ca_chain = request.get("ca_chain", "")
            action_params["ca-certificate"] = ca_cert
            if ca_chain:
                try:
                    action_params["ca-chain"] = generate_ca_chain(
                        request.get("certificate"), ca_cert, ca_chain
                    )
                except binascii.Error as e:
                    LOG.debug("Unable to encode CA chain: %s", e)
                    return Result(ResultType.FAILED, "Unable to encode CA chain")

            LOG.debug("Running provide-certificate for subject %s", subject)
            try:
                action_result = self.jhelper.run_action(
                    unit, self.model, "provide-certificate", action_params
                )
            except ActionFailedException as e:
                LOG.debug("provide-certificate failed for %s", subject)
                return Result(ResultType.FAILED, str(e))

            if action_result.get("return-code", 0) > 1:
                return Result(
                    ResultType.FAILED,
                    f"provide-certificate returned error for {subject}",
                )

        # Wait for Octavia to become active after certs are propagated via relation
        try:
            self.update_status(context, "waiting for Octavia to become active")
            self.jhelper.wait_until_active(
                self.model,
                apps=["octavia"],
                timeout=LOADBALANCER_CNI_DEPLOY_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(
                "Octavia did not reach active after providing certificates: %s", e
            )
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployAmphoraInfraStep(BaseStep, JujuStepHelper):
    """Deploy Multus and OpenStack Port CNI charms for Amphora support.

    Reads the persisted Amphora config from clusterd so that the
    NetworkAttachmentDefinition (NAD) is applied in the same Terraform run as
    the initial charm deployment — no separate NAD-update apply needed.
    """

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest,
        network_attachment_name: str = AMPHORA_NETWORK_ATTACHMENT_NAME,
    ):
        super().__init__(
            "Deploy Amphora Infrastructure",
            "Deploying Multus CNI and OpenStack Port CNI for Octavia Amphora",
        )
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.network_attachment_name = network_attachment_name
        self.model = OPENSTACK_MODEL

    def is_skip(self, context: StepContext) -> Result:
        """Skip when Amphora provider has been disabled by the user."""
        cfg = LoadbalancerAmphoraConfig(
            **questions.load_answers(
                self.deployment.get_client(), AMPHORA_CONFIG_SECTION
            )
        )
        if not cfg.amphora_enabled:
            return Result(ResultType.SKIPPED, "Amphora provider disabled")
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Deploy Multus and openstack-port-cni with NAD config in one apply."""
        cfg = LoadbalancerAmphoraConfig(
            **questions.load_answers(
                self.deployment.get_client(), AMPHORA_CONFIG_SECTION
            )
        )
        nad_yaml = _build_nad_yaml(
            self.network_attachment_name,
            cfg.lb_mgmt_network_id,
            cfg.lb_mgmt_subnet_id,
            cfg.lb_mgmt_secgroup_ids,
        )
        try:
            self.update_status(context, "deploying Multus and OpenStack Port CNI")
            self.tfhelper.update_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.manifest,
                tfvar_config=LOADBALANCER_CNI_CONFIG_KEY,
                override_tfvars={
                    "model_uuid": self.jhelper.get_model_uuid(self.model),
                    "multus-network-attachment-definitions": nad_yaml,
                },
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error deploying Amphora infrastructure")
            return Result(ResultType.FAILED, str(e))

        apps = ["multus", "openstack-port-cni"]
        LOG.debug("Applications monitored for readiness: %s", apps)
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, context.status)
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                apps,
                status=["active", "blocked"],
                timeout=LOADBALANCER_CNI_DEPLOY_TIMEOUT,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning("Timed out waiting for CNI applications %s: %s", apps, e)
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class UpdateCiliumCNIExclusiveStep(BaseStep):
    """Set or revert the Cilium CNI exclusive annotation for Multus support."""

    def __init__(
        self,
        deployment: Deployment,
        k8s_tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest,
        enable_multus: bool = True,
    ):
        description = (
            "Configuring Cilium to allow secondary interfaces via Multus"
            if enable_multus
            else "Restoring Cilium exclusive CNI mode"
        )
        super().__init__("Update Cilium CNI Exclusive", description)
        self.deployment = deployment
        self.k8s_tfhelper = k8s_tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.enable_multus = enable_multus

    def is_skip(self, context: StepContext) -> Result:
        """Skip if annotation is already in desired state or Amphora is disabled."""
        client = self.deployment.get_client()

        # When enabling: skip entirely if Amphora provider is not in use
        if self.enable_multus:
            cfg = LoadbalancerAmphoraConfig(
                **questions.load_answers(client, AMPHORA_CONFIG_SECTION)
            )
            if not cfg.amphora_enabled:
                return Result(ResultType.SKIPPED, "Amphora provider disabled")

        try:
            k8s_tfvars = read_config(client, K8S_CONFIG_KEY)
        except ConfigItemNotFoundException:
            if not self.enable_multus:
                return Result(ResultType.SKIPPED, "k8s config not found")
            return Result(ResultType.COMPLETED)

        cluster_annotations = k8s_tfvars.get("k8s_config", {}).get(
            "cluster-annotations", ""
        )
        annotation_present = CILIUM_EXCLUSIVE_ANNOTATION in cluster_annotations
        if self.enable_multus and annotation_present:
            return Result(ResultType.SKIPPED, "Cilium CNI exclusive already disabled")
        if not self.enable_multus and not annotation_present:
            return Result(
                ResultType.SKIPPED, "Cilium exclusive annotation already absent"
            )
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Add or remove the exclusive=false annotation from k8s cluster-annotations."""
        client = self.deployment.get_client()
        try:
            k8s_tfvars = read_config(client, K8S_CONFIG_KEY)
        except ConfigItemNotFoundException:
            k8s_tfvars = {}

        k8s_app_config = k8s_tfvars.get("k8s_config", {})

        if self.enable_multus:
            cluster_annotations = k8s_app_config.get(
                "cluster-annotations",
                "",
            )
            cluster_annotations = (
                f"{cluster_annotations} {CILIUM_EXCLUSIVE_ANNOTATION}".strip()
            )
        else:
            cluster_annotations = k8s_app_config.get("cluster-annotations", "")
            cluster_annotations = " ".join(
                a
                for a in cluster_annotations.split()
                if a != CILIUM_EXCLUSIVE_ANNOTATION
            )

        k8s_app_config["cluster-annotations"] = cluster_annotations

        try:
            self.k8s_tfhelper.update_tfvars_and_apply_tf(
                client,
                self.manifest,
                tfvar_config=K8S_CONFIG_KEY,
                override_tfvars={
                    "k8s_config": k8s_app_config,
                    "machine_model_uuid": self.jhelper.get_model_uuid(
                        self.deployment.openstack_machines_model
                    ),
                },
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error updating k8s cluster-annotations for Cilium")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveCNIInfraStep(BaseStep, JujuStepHelper):
    """Remove Multus and OpenStack Port CNI charms deployed for Amphora support."""

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Remove Amphora CNI Infrastructure",
            "Removing Multus CNI and OpenStack Port CNI for Octavia Amphora",
        )
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL

    def is_skip(self, context: StepContext) -> Result:
        """Skip if the CNI plan has no resources to destroy."""
        try:
            state = self.tfhelper.pull_state()
        except TerraformException:
            # State backend not initialised — CNI plan was never applied.
            LOG.debug("CNI terraform state unavailable; skipping", exc_info=True)
            return Result(ResultType.SKIPPED, "No CNI resources to remove")
        if not state.get("resources"):
            return Result(ResultType.SKIPPED, "No CNI resources to remove")
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Destroy Multus and openstack-port-cni via Terraform destroy."""
        try:
            self.update_status(context, "destroying Multus and OpenStack Port CNI")
            self.tfhelper.destroy()
            delete_config(self.deployment.get_client(), LOADBALANCER_CNI_CONFIG_KEY)
        except TerraformException as e:
            LOG.exception("Error removing Amphora CNI infrastructure")
            return Result(ResultType.FAILED, str(e))

        apps = ["multus", "openstack-port-cni"]
        LOG.debug("Waiting for applications to be removed: %s", apps)
        try:
            self.jhelper.wait_application_gone(
                apps,
                self.model,
                timeout=LOADBALANCER_CNI_DEPLOY_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.warning("Timed out waiting for CNI applications to be removed: %s", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CleanupMultusCNIFilesStep(BaseStep, JujuStepHelper):
    """Remove Multus CNI configuration files left in /etc/cni/net.d on each node.

    When the Multus DaemonSet is destroyed via Terraform it does not clean up
    the CNI configuration files it wrote to host directories on each k8s node.
    This step iterates over all k8s machine units and explicitly removes::

        /etc/cni/net.d/00-multus.conf

    Failures on individual nodes are logged as warnings but do not stop the
    disable plan.
    """

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__(
            "Cleanup Multus CNI files",
            "Removing Multus CNI configuration files from k8s nodes",
        )
        self.deployment = deployment
        self.jhelper = jhelper

    def run(self, context: StepContext) -> Result:
        """Delete Multus CNI files from every k8s machine unit."""
        try:
            app = self.jhelper.get_application(
                K8S_APP_NAME, self.deployment.openstack_machines_model
            )
        except (ApplicationNotFoundException, JujuException) as e:
            LOG.warning("Could not retrieve k8s application units: %s", e)
            return Result(ResultType.COMPLETED)

        cmd = "rm -f /etc/cni/net.d/00-multus.conf"
        for unit_name in app.units:
            try:
                result = self.jhelper.run_cmd_on_machine_unit_payload(
                    unit_name,
                    self.deployment.openstack_machines_model,
                    cmd,
                )
                if result.return_code != 0:
                    LOG.warning(
                        "Cleanup of Multus CNI files on %s exited %d: %s",
                        unit_name,
                        result.return_code,
                        result.stderr,
                    )
            except (ExecFailedException, JujuException) as e:
                LOG.warning(
                    "Failed to clean up Multus CNI files on %s: %s", unit_name, e
                )

        return Result(ResultType.COMPLETED)


class DestroyAmphoraResourcesStep(BaseStep):
    """Destroy OpenStack resources that were created by the setup Terraform plan.

    Only resources Terraform actually owns (those created with ``count = 1``)
    are destroyed.  User-provided resources (data-source lookups, ``count = 0``)
    are never in the Terraform state so they are never touched.

    Skipped automatically when the setup plan was never applied (no state).
    """

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
    ):
        super().__init__(
            "Destroy Amphora Resources",
            "Destroying Amphora flavor, security groups and lb-mgmt network",
        )
        self.deployment = deployment
        self.tfhelper = tfhelper

    def is_skip(self, context: StepContext) -> Result:
        """Skip if the setup plan has no resources to destroy."""
        try:
            state = self.tfhelper.pull_state()
        except TerraformException:
            LOG.debug(
                "Setup terraform state unavailable; skipping destroy",
                exc_info=True,
            )
            return Result(ResultType.SKIPPED, "No Amphora resources to destroy")
        if not state.get("resources"):
            return Result(ResultType.SKIPPED, "No Amphora resources to destroy")
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Destroy only the Terraform-owned OpenStack resources."""
        try:
            self.update_status(context, "destroying Amphora OpenStack resources")
            self.tfhelper.destroy()
            delete_config(self.deployment.get_client(), LOADBALANCER_SETUP_CONFIG_KEY)
        except TerraformException as e:
            LOG.exception("Error destroying Amphora OpenStack resources")
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class CreateAmphoraResourcesStep(BaseStep):
    """Create OpenStack resources required for Octavia Amphora via Terraform.

    Skipped when the user chose to provide resource IDs manually.
    After running, saves the Terraform output IDs back to clusterd so that
    UpdateOctaviaAmphoraConfigStep can consume them.
    """

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        manifest,
    ):
        super().__init__(
            "Create Amphora Resources",
            "Creating Amphora flavor, security groups and lb-mgmt network",
        )
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.manifest = manifest

    def is_skip(self, context: StepContext) -> Result:
        """Skip if Amphora is disabled or the user provided all resource IDs."""
        variables = questions.load_answers(
            self.deployment.get_client(), AMPHORA_CONFIG_SECTION
        )
        if not variables.get(_AMPHORA_ENABLED_KEY, True):
            return Result(ResultType.SKIPPED, "Amphora provider disabled")
        needs_creation = (
            variables.get(_AUTOCREATE_IMAGE_KEY, False)
            or variables.get(_AUTOCREATE_FLAVOR_KEY, True)
            or variables.get(_AUTOCREATE_NETWORK_KEY, True)
            or variables.get(_AUTOCREATE_SECGROUPS_KEY, True)
        )
        if not needs_creation:
            return Result(
                ResultType.SKIPPED,
                "All resource IDs provided; skipping Terraform resource creation",
            )
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Create only the resources the user left empty, save output IDs."""
        variables = questions.load_answers(
            self.deployment.get_client(), AMPHORA_CONFIG_SECTION
        )

        # Determine which resource groups need Terraform to create them
        create_image = bool(variables.get(_AUTOCREATE_IMAGE_KEY, False))
        create_flavor = bool(variables.get(_AUTOCREATE_FLAVOR_KEY, True))
        create_network = bool(variables.get(_AUTOCREATE_NETWORK_KEY, True))
        create_secgroups = bool(variables.get(_AUTOCREATE_SECGROUPS_KEY, True))

        LOG.debug(
            "Amphora resource creation flags:"
            " image=%s flavor=%s network=%s secgroups=%s",
            create_image,
            create_flavor,
            create_network,
            create_secgroups,
        )

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.manifest,
                tfvar_config=LOADBALANCER_SETUP_CONFIG_KEY,
                override_tfvars={
                    "create-amphora-image": create_image,
                    "create-amphora-flavor": create_flavor,
                    "create-lb-mgmt-network": create_network,
                    "create-lb-secgroups": create_secgroups,
                    # Pass existing IDs so Terraform data sources can look them
                    # up — keeps outputs always populated and allows CIDR to be
                    # derived automatically from the existing subnet.
                    "existing-amp-flavor-id": variables.get(_FLAVOR_KEY, ""),
                    "existing-lb-mgmt-network-id": variables.get(_NETWORK_KEY, ""),
                    "existing-lb-mgmt-subnet-id": variables.get(_SUBNET_KEY, ""),
                    # When user provides their own secgroups, pass them through
                    # so Terraform outputs are always populated.
                    "existing-lb-mgmt-secgroup-id": (
                        variables.get(_SECGROUPS_KEY, [""])[0]
                        if variables.get(_SECGROUPS_KEY)
                        else ""
                    ),
                    "existing-lb-health-secgroup-id": variables.get(
                        _HEALTH_SECGROUP_KEY, ""
                    ),
                    # lb-mgmt-cidr is hardcoded as IPv6 in main.tf; not passed.
                    # IPv4 alternative: uncomment the line below.
                    # "lb-mgmt-cidr": variables.get(
                    #     _NETWORK_CIDR_KEY, "172.31.0.0/24"
                    # ),
                    # Image tag used when create-amphora-image = true
                    "amphora-image-tag": variables.get(
                        _AMP_IMAGE_TAG_KEY, "octavia-amphora"
                    ),
                },
            )
            outputs = self.tfhelper.output()
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error creating Amphora OpenStack resources")
            return Result(ResultType.FAILED, str(e))

        # Outputs are always populated (data sources provide real IDs for
        # user-supplied resources).  Use them unconditionally; fall back to
        # whatever was already in clusterd only if an output is absent/empty.
        variables[_FLAVOR_KEY] = outputs.get("amphora-flavor-id") or variables.get(
            _FLAVOR_KEY, ""
        )
        variables[_NETWORK_KEY] = outputs.get("lb-mgmt-network-id") or variables.get(
            _NETWORK_KEY, ""
        )
        variables[_SUBNET_KEY] = outputs.get("lb-mgmt-subnet-id") or variables.get(
            _SUBNET_KEY, ""
        )
        if not variables.get(_SECGROUPS_KEY):
            mgmt_sg = outputs.get("lb-mgmt-secgroup-id", "")
            # Only the Amphora management security group belongs in
            # amp-secgroup-list.  The health-manager security group
            # (lb-health-mgr-sec-grp) is for health manager ports, not
            # for Amphora VM ports, so it is not passed to the charm.
            if mgmt_sg:
                variables[_SECGROUPS_KEY] = [mgmt_sg]
        if not variables.get(_HEALTH_SECGROUP_KEY):
            health_sg = outputs.get("lb-health-secgroup-id", "")
            if health_sg:
                variables[_HEALTH_SECGROUP_KEY] = health_sg

        questions.write_answers(
            self.deployment.get_client(), AMPHORA_CONFIG_SECTION, variables
        )
        return Result(ResultType.COMPLETED)


class UpdateOctaviaAmphoraConfigStep(BaseStep, JujuStepHelper):
    """Update Octavia charm config using persisted Amphora config.

    The Multus NAD is handled by ``DeployAmphoraInfraStep`` in the same
    Terraform apply as the charm deployment.  This step only updates the
    Octavia charm application configuration.
    """

    def __init__(
        self,
        deployment: Deployment,
        openstack_tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest,
        network_attachment_name: str = AMPHORA_NETWORK_ATTACHMENT_NAME,
    ):
        super().__init__(
            "Configure Octavia Amphora",
            "Updating Octavia charm configuration for Amphora provider",
        )
        self.deployment = deployment
        self.openstack_tfhelper = openstack_tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.network_attachment_name = network_attachment_name
        self.model = OPENSTACK_MODEL

    def run(self, context: StepContext) -> Result:
        """Load persisted Amphora config and update the Octavia charm config."""
        from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS

        client = self.deployment.get_client()
        cfg = LoadbalancerAmphoraConfig(
            **questions.load_answers(client, AMPHORA_CONFIG_SECTION)
        )

        if not cfg.amphora_enabled:
            # Clear all Amphora config from the Octavia charm so it no longer
            # tries to use the Amphora provider.
            LOG.debug("Amphora disabled: clearing octavia-config from charm")
            octavia_amphora_config: dict = {}
        else:
            LOG.debug(
                "Amphora config: image_tag=%s, flavor=%s, net=%s,"
                " subnet=%s, secgroups=%s",
                cfg.amp_image_tag,
                cfg.amp_flavor_id,
                cfg.lb_mgmt_network_id,
                cfg.lb_mgmt_subnet_id,
                cfg.lb_mgmt_secgroup_ids,
            )

            octavia_amphora_config = {
                "amphora-network-attachment": (
                    f"{OPENSTACK_MODEL}/{self.network_attachment_name}"
                ),
                "amp-image-tag": cfg.amp_image_tag,
            }
            if cfg.amp_flavor_id:
                octavia_amphora_config["amp-flavor-id"] = cfg.amp_flavor_id
            if cfg.lb_mgmt_secgroup_ids:
                octavia_amphora_config["amp-secgroup-list"] = ",".join(
                    cfg.lb_mgmt_secgroup_ids
                )
            if cfg.lb_mgmt_network_id:
                octavia_amphora_config["amp-boot-network-list"] = cfg.lb_mgmt_network_id

        override_tfvars = {
            "octavia-config": octavia_amphora_config,
            # Point Octavia at manual-tls-certificates when Amphora is enabled;
            # clear it when disabled so Octavia stops waiting for certs.
            "octavia-to-tls-provider": (
                CA_MANUAL_TLS_CERTIFICATE if cfg.amphora_enabled else None
            ),
            # Ensure the charm is deployed from the correct channel.
            "manual-tls-certificates-channel": MANUAL_TLS_CERTIFICATES_CHANNEL
            if cfg.amphora_enabled
            else None,
        }
        try:
            self.openstack_tfhelper.update_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.manifest,
                tfvar_config=OPENSTACK_TERRAFORM_VARS,
                override_tfvars=override_tfvars,
                tf_apply_extra_args=[
                    # Update the Octavia application config.
                    "-target=module.octavia[0].juju_application.service",
                    # Deploy (or remove) the manual-tls-certificates application;
                    # its `count` depends on octavia-to-tls-provider.
                    "-target=juju_application.manual-tls-certificates[0]",
                    # Create (or destroy) the amphora-issuing-ca integration.
                    "-target=juju_integration.octavia-to-ca_amphora_issuing_ca[0]",
                    # Create (or destroy) the amphora-controller-cert integration.
                    "-target=juju_integration.octavia-to-ca_amphora_controller_cert[0]",
                ],
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Error updating Octavia charm config for Amphora")
            return Result(ResultType.FAILED, str(e))

        # When Amphora is enabled Octavia will be blocked until TLS certificates
        # are provided via ``sunbeam loadbalancer provide-certificates``.  Wait
        # specifically for the CA-cert-missing blocked message so we know the
        # Amphora config has been fully applied (or octavia is already active).
        # Require agent=idle to avoid returning during the brief (~6 s) inter-hook
        # active/idle windows that appear after the pod-template annotation patch
        # triggers a Kubernetes rolling update and the charm runs its post-restart
        # hook sequence (upgrade-charm → config-changed → pebble-ready hooks).
        octavia_overlay: ApplicationStatusOverlay = {
            "status": ["active", "blocked"],
            "workload_status_message": [
                OCTAVIA_AMPHORA_RELATIONS_MISSING_MESSAGE,
                OCTAVIA_AMPHORA_CA_CERT_MESSAGE,
                OCTAVIA_AMPHORA_CONTROLLER_CERT_MESSAGE,
            ],
        }
        try:
            self.update_status(context, "waiting for Octavia to settle")
            apps = ["octavia"]
            status_queue: queue.Queue[str] = queue.Queue()
            task = update_status_background(self, apps, status_queue, context.status)
            try:
                self.jhelper.wait_until_desired_status(
                    self.model,
                    apps,
                    status=["active", "blocked"],
                    agent_status=["idle"],
                    timeout=LOADBALANCER_CNI_DEPLOY_TIMEOUT,
                    queue=status_queue,
                    overlay={"octavia": octavia_overlay},
                )
            finally:
                task.stop()
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning("Octavia did not reach stable state after config update: %s", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class LoadbalancerFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "loadbalancer"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    @property
    def requires(self) -> set[FeatureRequirement]:  # type: ignore[override]
        """Require Barbican (secrets) only when Amphora is actually configured.

        Checks both the snap feature gate (coarse guard) and the persisted
        ``amphora_enabled`` key in clusterd (fine-grained user choice).
        """
        if not is_feature_gate_enabled("feature.loadbalancer-amphora"):
            return set()
        try:
            saved = questions.load_answers(Client.from_socket(), AMPHORA_CONFIG_SECTION)
            if not saved.get(_AMPHORA_ENABLED_KEY, False):
                return set()
        except Exception:
            # Not running inside the snap or cluster unreachable — fall back to
            # requiring secrets so the gate is not silently dropped.
            LOG.debug(
                "Could not read clusterd amphora config; defaulting to requiring"
                " secrets",
                exc_info=True,
            )
        return {FeatureRequirement("secrets")}

    def __init__(self) -> None:
        super().__init__()
        self.cni_tfplan = LOADBALANCER_CNI_TFPLAN
        self.cni_tfplan_dir = LOADBALANCER_CNI_TFPLAN_DIR
        self.setup_tfplan = LOADBALANCER_SETUP_TFPLAN
        self.setup_tfplan_dir = LOADBALANCER_SETUP_TFPLAN_DIR

    def config_type(self) -> type[LoadbalancerFeatureConfig]:
        """Return manifest config type for the loadbalancer feature."""
        return LoadbalancerFeatureConfig

    def preseed_questions_content(self) -> list:
        """Generate preseed manifest content for features.loadbalancer.config."""
        resource_bank = questions.QuestionBank(
            questions=_amphora_questions(),
            console=console,
            previous_answers={},
        )
        content = questions.show_questions(
            resource_bank,
            comment_out=True,
        )
        cert_bank = questions.QuestionBank(
            questions=_amphora_certificate_questions("app", "unit", "subject"),
            console=console,
            previous_answers={},
        )
        content += questions.show_questions(
            cert_bank,
            section="certificates",
            subsection="<CSR x500UniqueIdentifier>",
            section_description=(
                "Octavia Amphora TLS certificates, keyed by CSR subject."
                " Run 'sunbeam loadbalancer list-outstanding-csrs' to get subjects."
            ),
            comment_out=True,
        )
        return content

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "octavia-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "multus": CharmManifest(channel=MULTUS_CHANNEL),
                "openstack-port-cni-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "manual-tls-certificates": CharmManifest(
                    channel=MANUAL_TLS_CERTIFICATES_CHANNEL
                ),
            },
            terraform={
                self.cni_tfplan: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.cni_tfplan_dir
                ),
                self.setup_tfplan: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.setup_tfplan_dir
                ),
            },
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "octavia-k8s": {
                        "channel": "octavia-channel",
                        "revision": "octavia-revision",
                        "config": "octavia-config",
                    },
                    "manual-tls-certificates": {
                        "channel": "manual-tls-certificates-channel",
                        "revision": "manual-tls-certificates-revision",
                        "config": "manual-tls-certificates-config",
                    },
                }
            },
            self.cni_tfplan: {
                "charms": {
                    "multus": {
                        "channel": "multus-channel",
                        "revision": "multus-revision",
                        "config": "multus-config",
                    },
                    "openstack-port-cni-k8s": {
                        "channel": "openstack-port-cni-channel",
                        "revision": "openstack-port-cni-revision",
                        "config": "openstack-port-cni-config",
                    },
                }
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = ["octavia", "octavia-mysql-router"]
        if self.get_database_topology(deployment) == "multi":
            apps.extend(["octavia-mysql"])

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-octavia": True,
            **self.add_horizon_plugin_to_tfvars(deployment, "octavia"),
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-octavia": False,
            "octavia-config": {},
            "octavia-to-tls-provider": None,
            **self.remove_horizon_plugin_from_tfvars(deployment, "octavia"),
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "octavia": {"octavia-k8s": 6},
        }

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Run plans to enable the loadbalancer feature.

        Only deploys Octavia here.  Multus, openstack-port-cni and the Cilium
        annotation update are handled in ``run_configure_plans`` so they only
        run when the user actually enables the Amphora provider.
        """
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(AddManifestStep(deployment.get_client(), self.user_manifest))

        octavia_overlay: ApplicationStatusOverlay = {
            "status": ["active", "waiting", "blocked"],
            "workload_status_message": [
                OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE,
                OCTAVIA_AMPHORA_RELATIONS_MISSING_MESSAGE,
                OCTAVIA_AMPHORA_CA_CERT_MESSAGE,
                OCTAVIA_AMPHORA_CONTROLLER_CERT_MESSAGE,
            ],
        }
        plan.extend(
            [
                TerraformInitStep(tfhelper),
                EnableOpenStackApplicationStep(
                    deployment,
                    config,
                    tfhelper,
                    jhelper,
                    self,
                    app_desired_status=["active", "waiting", "blocked"],
                    overlay={"octavia": octavia_overlay},
                ),
            ]
        )

        run_plan(plan, console, show_hints)
        click.echo("OpenStack Loadbalancer application enabled.")

    def run_configure_plans(
        self,
        deployment: Deployment,
        show_hints: bool,
        accept_defaults: bool = False,
    ) -> None:
        """Prompt for Amphora config then run either the enable or disable plan.

        Phase 1 — AmphoraConfigStep interactively asks the user (or accepts
        defaults) and persists the decision to clusterd.

        Phase 2 — branches on the stored ``amphora_enabled`` flag:

        Enable path:
          - CreateAmphoraResourcesStep: creates missing OpenStack resources
          - DeployAmphoraInfraStep: deploys Multus + openstack-port-cni with NAD
          - UpdateCiliumCNIExclusiveStep: sets Cilium to non-exclusive mode
          - UpdateOctaviaAmphoraConfigStep: pushes config + sets TLS provider,
            waits for Octavia active or blocked (blocked = awaiting certs)

        Disable path (tears down what enable set up):
          - RemoveCNIInfraStep: destroys Multus + openstack-port-cni
          - UpdateCiliumCNIExclusiveStep: reverts Cilium to exclusive mode
          - UpdateOctaviaAmphoraConfigStep: clears Amphora config from charm

        TLS certificates are provisioned separately via
        ``sunbeam loadbalancer provide-certificates``.
        """
        openstack_tfhelper = deployment.get_tfhelper(self.tfplan)
        cni_tfhelper = deployment.get_tfhelper(self.cni_tfplan)
        setup_tfhelper = deployment.get_tfhelper(self.setup_tfplan)
        k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
        jhelper = JujuHelper(deployment.juju_controller)

        feature_config: LoadbalancerFeatureConfig | None = None
        if self.manifest:
            feature_manifest = self.manifest.get_feature("loadbalancer")
            if feature_manifest and isinstance(
                feature_manifest.config, LoadbalancerFeatureConfig
            ):
                feature_config = feature_manifest.config

        # Read the previous state before prompting so we can detect a
        # no-op disable (amphora was never enabled, nothing to tear down).
        # Default to False: an empty dict means "never configured".
        previous_answers = questions.load_answers(
            deployment.get_client(), AMPHORA_CONFIG_SECTION
        )
        previously_enabled = previous_answers.get(_AMPHORA_ENABLED_KEY, False)

        # Phase 1: all user prompts in one place — resource config + cert CSRs.
        run_plan(
            [
                AmphoraConfigStep(
                    deployment,
                    feature_config,
                    jhelper,
                    accept_defaults,
                )
            ],
            console,
            show_hints,
        )

        # Read back the decision written by AmphoraConfigStep.
        amphora_enabled = questions.load_answers(
            deployment.get_client(), AMPHORA_CONFIG_SECTION
        ).get(_AMPHORA_ENABLED_KEY, True)

        if not amphora_enabled:
            if not previously_enabled:
                # Amphora was never enabled — nothing to tear down.
                click.echo("Octavia Amphora provider disabled.")
                return

            # Disable path: tear down CNI infra, revert Cilium, clear Octavia config.
            plan: list[BaseStep] = [
                TerraformInitStep(cni_tfhelper),
                RemoveCNIInfraStep(deployment, cni_tfhelper, jhelper),
                CleanupMultusCNIFilesStep(deployment, jhelper),
                TerraformInitStep(k8s_tfhelper),
                UpdateCiliumCNIExclusiveStep(
                    deployment,
                    k8s_tfhelper,
                    jhelper,
                    self.manifest,
                    enable_multus=False,
                ),
                TerraformInitStep(openstack_tfhelper),
                UpdateOctaviaAmphoraConfigStep(
                    deployment, openstack_tfhelper, jhelper, self.manifest
                ),
            ]
            run_plan(plan, console, show_hints)
            click.echo("Octavia Amphora provider disabled.")
        else:
            # Enable path: credentials are only needed here
            # (OpenStack resource creation).
            jhelper_keystone = deployment.get_juju_helper(keystone=True)
            admin_credentials = retrieve_admin_credentials(
                jhelper_keystone, deployment, OPENSTACK_MODEL
            )
            admin_credentials["OS_INSECURE"] = "true"
            setup_tfhelper.env = (setup_tfhelper.env or {}) | admin_credentials

            plan = [
                TerraformInitStep(setup_tfhelper),
                CreateAmphoraResourcesStep(
                    deployment,
                    setup_tfhelper,
                    self.manifest,
                ),
                TerraformInitStep(cni_tfhelper),
                DeployAmphoraInfraStep(
                    deployment,
                    cni_tfhelper,
                    jhelper,
                    self.manifest,
                ),
                TerraformInitStep(k8s_tfhelper),
                UpdateCiliumCNIExclusiveStep(
                    deployment,
                    k8s_tfhelper,
                    jhelper,
                    self.manifest,
                    enable_multus=True,
                ),
                UpdateOctaviaAmphoraConfigStep(
                    deployment,
                    openstack_tfhelper,
                    jhelper,
                    self.manifest,
                ),
            ]
            run_plan(plan, console, show_hints)
            click.echo("Octavia Amphora configuration applied.")

    def configure_hook(
        self,
        deployment: Deployment,
    ) -> None:
        """Configure hook - re-apply Amphora config if previously configured."""
        client = deployment.get_client()
        if not self.is_enabled(client):
            return
        saved = questions.load_answers(client, AMPHORA_CONFIG_SECTION)
        if not saved or not saved.get(_AMPHORA_ENABLED_KEY, True):
            LOG.debug("Skipping Amphora configure hook: not configured or disabled")
            return
        try:
            self.run_configure_plans(deployment, show_hints=False, accept_defaults=True)
        except Exception as e:
            LOG.warning(
                "Amphora configure hook failed (non-fatal): %s",
                str(e),
                exc_info=True,
            )

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable Loadbalancer service."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable the loadbalancer feature.

        In addition to disabling the octavia application, also removes the
        Multus and openstack-port-cni-k8s charms, reverts the k8s
        cluster-annotations to restore Cilium exclusive CNI mode, and destroys
        any OpenStack resources (flavor, network, security groups) that were
        created by the setup Terraform plan.  User-provided resources are never
        in Terraform state so they are never touched.
        """
        tfhelper = deployment.get_tfhelper(self.tfplan)
        cni_tfhelper = deployment.get_tfhelper(self.cni_tfplan)
        setup_tfhelper = deployment.get_tfhelper(self.setup_tfplan)
        k8s_tfhelper = deployment.get_tfhelper("k8s-plan")
        jhelper = JujuHelper(deployment.juju_controller)

        # Admin credentials are required so the setup Terraform plan can
        # authenticate against OpenStack to destroy its resources.
        jhelper_keystone = deployment.get_juju_helper(keystone=True)
        admin_credentials = retrieve_admin_credentials(
            jhelper_keystone, deployment, OPENSTACK_MODEL
        )
        admin_credentials["OS_INSECURE"] = "true"
        setup_tfhelper.env = (setup_tfhelper.env or {}) | admin_credentials

        plan: list[BaseStep] = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
            TerraformInitStep(cni_tfhelper),
            RemoveCNIInfraStep(deployment, cni_tfhelper, jhelper),
            CleanupMultusCNIFilesStep(deployment, jhelper),
            TerraformInitStep(k8s_tfhelper),
            UpdateCiliumCNIExclusiveStep(
                deployment, k8s_tfhelper, jhelper, self.manifest, enable_multus=False
            ),
            TerraformInitStep(setup_tfhelper),
            DestroyAmphoraResourcesStep(deployment, setup_tfhelper),
        ]

        run_plan(plan, console, show_hints)
        click.echo("OpenStack Loadbalancer application disabled.")

    def post_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Clear Amphora config from clusterd after the feature is disabled."""
        super().post_disable(deployment, show_hints)
        client = deployment.get_client()
        for key in (AMPHORA_CONFIG_SECTION, AMPHORA_CERTIFICATES_CONFIG):
            try:
                delete_config(client, key)
            except Exception:
                LOG.debug("Config key %r not found or already removed", key)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Loadbalancer service."""
        self.disable_feature(deployment, show_hints)

    @click.group()
    def loadbalancer_groups(self):
        """Manage Loadbalancer feature."""

    @feature_gate_command(gate_key="feature.loadbalancer-amphora")
    @click.command()
    @click.option(
        "-a",
        "--accept-defaults",
        is_flag=True,
        default=False,
        help="Accept all defaults without prompting.",
    )
    @click_option_show_hints
    @pass_method_obj
    def configure(
        self,
        deployment: Deployment,
        accept_defaults: bool,
        show_hints: bool,
    ) -> None:
        r"""Configure Octavia Amphora resources.

        Prompts for resource configuration.  Confirm auto-create options (the
        default) or supply existing resource IDs:

        \b
        - Amphora image tag        — always required (default: octavia-amphora)
        - Auto-create image?       — confirm to download+upload via Terraform
        - Auto-create flavor?      — confirm to create a dedicated Nova flavor
        - Auto-create network?     — confirm to create lb-mgmt network + subnet
        - Auto-create sec-groups?  — confirm to create security groups

        After configuring resources, Octavia is updated with the TLS provider
        set to ``manual-tls-certificates``.  Octavia will be in a *blocked*
        state until certificates are provided.  Run:

          sunbeam loadbalancer provide-certificates

        Configuration is saved and re-applied automatically during
        'sunbeam configure'.
        """
        if not deployment.get_feature_manager().is_feature_enabled(
            deployment, "secrets"
        ):
            raise click.ClickException(
                "Barbican (secrets) feature must be enabled for Octavia Amphora.\n"
                "Enable it first: sunbeam enable secrets"
            )
        self.run_configure_plans(
            deployment,
            show_hints=show_hints,
            accept_defaults=accept_defaults,
        )

    @feature_gate_command(gate_key="feature.loadbalancer-amphora")
    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def provide_certificates(
        self,
        deployment: Deployment,
        show_hints: bool,
    ) -> None:
        """Provide TLS certificates to Octavia Amphora.

        For each outstanding CSR registered by the Octavia charm with the
        manual-tls-certificates operator, prompts for a base64-encoded signed
        certificate, a required CA certificate, and an optional CA chain.
        After providing all certificates, waits for Octavia to become active.

        List pending CSRs first with:

          sunbeam loadbalancer list-outstanding-csrs
        """
        if not deployment.get_feature_manager().is_feature_enabled(
            deployment, "secrets"
        ):
            raise click.ClickException(
                "Barbican (secrets) feature must be enabled for Octavia Amphora.\n"
                "Enable it first: sunbeam enable secrets"
            )
        jhelper = JujuHelper(deployment.juju_controller)
        feature_config: LoadbalancerFeatureConfig | None = None
        if self.manifest:
            feature_manifest = self.manifest.get_feature("loadbalancer")
            if feature_manifest and isinstance(
                feature_manifest.config, LoadbalancerFeatureConfig
            ):
                feature_config = feature_manifest.config
        run_plan(
            [
                ProvideCertificatesStep(
                    deployment,
                    feature_config,
                    jhelper,
                )
            ],
            console,
            show_hints,
        )
        click.echo("TLS certificates provided to Octavia.")

    @feature_gate_command(gate_key="feature.loadbalancer-amphora")
    @click.command()
    @click.option(
        "--format",
        type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
        default=FORMAT_TABLE,
        help="Output format",
    )
    @pass_method_obj
    def list_outstanding_csrs(self, deployment: Deployment, format: str) -> None:
        """List outstanding Certificate Signing Requests for Octavia Amphora."""
        if not deployment.get_feature_manager().is_feature_enabled(
            deployment, "secrets"
        ):
            raise click.ClickException(
                "Barbican (secrets) feature must be enabled for Octavia Amphora.\n"
                "Enable it first: sunbeam enable secrets"
            )
        csrs = handle_list_outstanding_csrs(
            CA_MANUAL_TLS_CERTIFICATE,
            CA_MANUAL_TLS_CERTIFICATE_INTERFACE,
            OPENSTACK_MODEL,
            deployment,
        )
        for record in csrs:
            record["endpoint"] = (
                "amphora-issuing-ca"
                if "issuing-ca" in _csr_common_name(record.get("csr") or "").lower()
                else "amphora-controller-cert"
            )
        if format == FORMAT_TABLE:
            table = Table()
            table.add_column("App")
            table.add_column("Unit")
            table.add_column("Relation ID")
            table.add_column("Endpoint")
            table.add_column("CSR")
            for record in csrs:
                table.add_row(
                    record.get("app_name"),
                    record.get("unit_name"),
                    record.get("relation_id"),
                    record.get("endpoint"),
                    record.get("csr"),
                )
            console.print(table)
        elif format == FORMAT_YAML:
            yaml.add_representer(str, str_presenter)
            console.print(yaml.dump(csrs))

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "init": [{"name": "loadbalancer", "command": self.loadbalancer_groups}],
            "init.loadbalancer": [
                {"name": "configure", "command": self.configure},
                {
                    "name": "provide_certificates",
                    "command": self.provide_certificates,
                },
                {
                    "name": "list_outstanding_csrs",
                    "command": self.list_outstanding_csrs,
                },
            ],
        }
