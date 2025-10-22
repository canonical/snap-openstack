# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path

import click
import pydantic
import yaml
from packaging.version import Version
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core import questions
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    read_config,
    run_plan,
    str_presenter,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
)
from sunbeam.core.manifest import (
    AddManifestStep,
    FeatureConfig,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.utils import (
    validate_ca_certificate,
    validate_ca_chain,
)
from sunbeam.features.interface.v1.openstack import (
    TerraformPlanLocation,
    WaitForApplicationsStep,
)
from sunbeam.features.tls.common import (
    CA_MANUAL_TLS_CERTIFICATE,
    CA_MANUAL_TLS_CERTIFICATE_INTERFACE,
    CERTIFICATE_FEATURE_KEY,
    INGRESS_CHANGE_APPLICATION_TIMEOUT,
    ConfigureTLSCertificatesStep,
    TlsFeature,
    TlsFeatureConfig,
    certificate_questions,
    handle_list_outstanding_csrs,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

LOG = logging.getLogger(__name__)
console = Console()


class _Certificate(pydantic.BaseModel):
    certificate: str


class CaTlsFeatureConfig(TlsFeatureConfig):
    certificates: dict[str, _Certificate] = {}


class CaTlsFeature(TlsFeature):
    version = Version("0.0.1")

    name = "tls.ca"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def config_type(self) -> type | None:
        """Return the config type for the feature."""
        return CaTlsFeatureConfig

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
        return content

    @click.command()
    @click.option(
        "--endpoint",
        "endpoints",
        multiple=True,
        default=["public"],
        type=click.Choice(["public", "internal", "rgw"], case_sensitive=False),
        help="Specify endpoints to apply tls.",
    )
    @click.option(
        "--ca-chain",
        required=False,
        type=str,
        default=None,
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
        ca_chain: str | None,
        endpoints: list[str],
        show_hints: bool,
    ):
        """Enable CA feature."""
        self.enable_feature(
            deployment,
            CaTlsFeatureConfig(ca=ca, ca_chain=ca_chain, endpoints=endpoints),
            show_hints,
        )

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool):
        """Disable CA feature."""
        self.disable_feature(deployment, show_hints)
        console.print("CA feature disabled")

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return ["manual-tls-certificates"]

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: CaTlsFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        tfvars: dict[str, str | bool] = {
            "traefik-to-tls-provider": CA_MANUAL_TLS_CERTIFICATE,
            "manual-tls-certificates-channel": "1/stable",
        }
        if "public" in config.endpoints:
            tfvars.update({"enable-tls-for-public-endpoint": True})
        if "internal" in config.endpoints:
            tfvars.update({"enable-tls-for-internal-endpoint": True})
        if "rgw" in config.endpoints:
            tfvars.update({"enable-tls-for-rgw-endpoint": True})

        return tfvars

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        tfvars: dict[str, None | str | bool] = {"traefik-to-tls-provider": None}
        provider_config = self.provider_config(deployment)
        endpoints = provider_config.get("endpoints", [])
        if "public" in endpoints:
            tfvars.update({"enable-tls-for-public-endpoint": False})
        if "internal" in endpoints:
            tfvars.update({"enable-tls-for-internal-endpoint": False})
        if "rgw" in endpoints:
            tfvars.update({"enable-tls-for-rgw-endpoint": False})

        return tfvars

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.group()
    def ca_group(self) -> None:
        """Manage CA."""

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
        csrs = handle_list_outstanding_csrs(
            CA_MANUAL_TLS_CERTIFICATE,
            CA_MANUAL_TLS_CERTIFICATE_INTERFACE,
            OPENSTACK_MODEL,
            deployment,
        )

        if format == FORMAT_TABLE:
            table = Table()
            table.add_column("Unit name")
            table.add_column("CSR")
            for unit, csr in csrs.items():
                table.add_row(unit, csr)
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
        apps_to_monitor = ["traefik", "traefik-public", "keystone"]
        if client.cluster.list_nodes_by_role("storage"):
            apps_to_monitor.append("traefik-rgw")

        try:
            config = read_config(client, CERTIFICATE_FEATURE_KEY)
        except ConfigItemNotFoundException:
            config = {}
        ca = config.get("ca")
        ca_chain = config.get("chain")

        if ca is None:
            raise click.ClickException("CA is not configured")

        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            AddManifestStep(client, manifest_path),
            ConfigureTLSCertificatesStep(
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
            "init.tls": [{"name": "ca", "command": self.ca_group}],
            "init.tls.ca": [
                {"name": "unit_certs", "command": self.configure},
                {
                    "name": "list_outstanding_csrs",
                    "command": self.list_outstanding_csrs,
                },
            ],
        }
