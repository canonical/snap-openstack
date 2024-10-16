# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging

import click
import yaml
from rich.console import Console
from rich.table import Column, Table

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    Result,
    ResultType,
    Status,
    convert_proxy_to_model_configs,
    run_plan,
    update_config,
)
from sunbeam.core.deployment import PROXY_CONFIG_KEY, Deployment
from sunbeam.core.juju import CONTROLLER_MODEL, JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.questions import (
    ConfirmQuestion,
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.steps.juju import UpdateJujuModelConfigStep
from sunbeam.steps.openstack import UpdateOpenStackModelConfigStep
from sunbeam.steps.sunbeam_machine import DeploySunbeamMachineApplicationStep

LOG = logging.getLogger(__name__)
console = Console()


def _preflight_checks(deployment: Deployment):
    from sunbeam.provider.maas.deployment import MAAS_TYPE  # to avoid circular import

    client = deployment.get_client()
    if deployment.type == MAAS_TYPE:
        if client is None:
            message = (
                "Deployment not bootstrapped or bootstrap process has not "
                "completed succesfully. Please run `sunbeam cluster bootstrap`"
            )
            raise click.ClickException(message)
    preflight_checks = [VerifyBootstrappedCheck(client)]

    run_preflight_checks(preflight_checks, console)


def _update_proxy(proxy: dict, deployment: Deployment):
    from sunbeam.provider.maas.deployment import MAAS_TYPE  # to avoid circular import

    _preflight_checks(deployment)
    client = deployment.get_client()

    # Update proxy in clusterdb
    update_config(client, PROXY_CONFIG_KEY, proxy)

    jhelper = JujuHelper(deployment.get_connected_controller())
    manifest = deployment.get_manifest()
    proxy_settings = deployment.get_proxy_settings()
    model_config = convert_proxy_to_model_configs(proxy_settings)

    plan: list[BaseStep] = []
    plan.append(
        DeploySunbeamMachineApplicationStep(
            deployment,
            client,
            deployment.get_tfhelper("sunbeam-machine-plan"),
            jhelper,
            manifest,
            deployment.openstack_machines_model,
            refresh=True,
            proxy_settings=proxy_settings,
        )
    )
    plan.append(
        UpdateJujuModelConfigStep(
            jhelper, CONTROLLER_MODEL.split("/")[-1], model_config
        )
    )
    if deployment.type == MAAS_TYPE:
        plan.append(
            UpdateJujuModelConfigStep(
                jhelper, deployment.openstack_machines_model, model_config
            )
        )
    else:
        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        plan.append(TerraformInitStep(openstack_tfhelper))
        plan.append(
            UpdateOpenStackModelConfigStep(
                client, openstack_tfhelper, manifest, model_config
            )
        )
    run_plan(plan, console)

    deployment.get_feature_manager().update_proxy_model_configs(deployment)


@click.command()
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def show(ctx: click.Context, format: str) -> None:
    """Show proxy configuration."""
    deployment: Deployment = ctx.obj
    _preflight_checks(deployment)

    proxy = deployment.get_proxy_settings()
    if format == FORMAT_TABLE:
        table = Table(
            Column("Proxy Variable"),
            Column("Value"),
            title="Proxy configuration",
        )
        for proxy_variable, value in proxy.items():
            table.add_row(proxy_variable, value)
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(proxy))


@click.command()
@click.option("--no-proxy", type=str, prompt=True, help="NO_PROXY configuration")
@click.option("--https-proxy", type=str, prompt=True, help="HTTPS_PROXY configuration")
@click.option("--http-proxy", type=str, prompt=True, help="HTTP_PROXY configuration")
@click.pass_context
def set(ctx: click.Context, http_proxy: str, https_proxy: str, no_proxy: str) -> None:
    """Update proxy configuration."""
    deployment: Deployment = ctx.obj

    if not (http_proxy and https_proxy and no_proxy):
        click.echo("ERROR: Expected atleast one of http_proxy, https_proxy, no_proxy")
        click.echo("To clear the proxy, use command `sunbeam proxy clear`")
        return

    variables: dict[str, dict[str, str | bool]] = {"proxy": {}}
    variables["proxy"]["proxy_required"] = True
    variables["proxy"]["http_proxy"] = http_proxy
    variables["proxy"]["https_proxy"] = https_proxy
    variables["proxy"]["no_proxy"] = no_proxy
    try:
        _update_proxy(variables, deployment)
    except (ClusterServiceUnavailableException, ConfigItemNotFoundException) as e:
        LOG.debug(f"Exception in updating config {str(e)}")
        click.echo("ERROR: Not able to update proxy config: str(e)")
        return


@click.command()
@click.pass_context
def clear(ctx: click.Context) -> None:
    """Clear proxy configuration."""
    deployment: Deployment = ctx.obj

    variables: dict[str, dict[str, str | bool]] = {"proxy": {}}
    variables["proxy"]["proxy_required"] = False
    variables["proxy"]["http_proxy"] = ""
    variables["proxy"]["https_proxy"] = ""
    variables["proxy"]["no_proxy"] = ""
    try:
        _update_proxy(variables, deployment)
    except (ClusterServiceUnavailableException, ConfigItemNotFoundException) as e:
        LOG.debug(f"Exception in updating config {str(e)}")
        click.echo("ERROR: Not able to clear proxy config: str(e)")
        return


def does_not_contain_quotes(answer: str):
    """Check if the answer does not contain quotes."""
    if '"' in answer or "'" in answer:
        raise ValueError("Answer cannot contain quotes (\" or ').")


def proxy_questions():
    return {
        "proxy_required": ConfirmQuestion(
            "Use proxy to access external network resources?",
            default_value=False,
        ),
        "http_proxy": PromptQuestion(
            "http_proxy (url):",
            validation_function=does_not_contain_quotes,
        ),
        "https_proxy": PromptQuestion(
            "https_proxy (url):",
            validation_function=does_not_contain_quotes,
        ),
        "no_proxy": PromptQuestion(
            "no_proxy (comma separated domain/IP/cidr):",
            validation_function=does_not_contain_quotes,
        ),
    }


class PromptForProxyStep(BaseStep):
    client: Client | None

    def __init__(
        self,
        deployment: Deployment,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Proxy Settings", "Query user for proxy settings")
        self.deployment = deployment
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        try:
            self.client = deployment.get_client()
        except ValueError:
            # For MAAS deployment, client is not set at this point
            self.client = None
        self.variables: dict = {}

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        if self.client:
            self.variables = load_answers(self.client, PROXY_CONFIG_KEY)
        self.variables.setdefault("proxy", {})

        previous_answers = self.variables.get("proxy", {})
        LOG.debug(f"Previos anders: {previous_answers}")
        if not (
            previous_answers.get("http_proxy")
            and previous_answers.get("https_proxy")  # noqa: W503
            and previous_answers.get("no_proxy")  # noqa: W503
        ):
            # Fill with defaults coming from deployment default_proxy_settings
            default_proxy_settings = self.deployment.get_default_proxy_settings()
            default_proxy_settings = {
                k.lower(): v for k, v in default_proxy_settings.items() if v
            }

            # If proxies are coming from defaults, change the default for
            # proxy_required to True. For example in local provider deployment,
            # default for proxy_required will be "y" if proxies exists in
            # /etc/environment
            if default_proxy_settings:
                previous_answers["proxy_required"] = True

            previous_answers.update(default_proxy_settings)

        preseed = {}
        if self.manifest and (proxy := self.manifest.core.config.proxy):
            preseed = proxy.model_dump(by_alias=True)

        proxy_bank = QuestionBank(
            questions=proxy_questions(),
            console=console,
            preseed=preseed,
            previous_answers=previous_answers,
            accept_defaults=self.accept_defaults,
        )

        self.variables["proxy"]["proxy_required"] = proxy_bank.proxy_required.ask()
        if self.variables["proxy"]["proxy_required"]:
            self.variables["proxy"]["http_proxy"] = proxy_bank.http_proxy.ask()
            self.variables["proxy"]["https_proxy"] = proxy_bank.https_proxy.ask()
            self.variables["proxy"]["no_proxy"] = proxy_bank.no_proxy.ask()

        if self.client:
            write_answers(self.client, PROXY_CONFIG_KEY, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        return Result(ResultType.COMPLETED, self.variables)
