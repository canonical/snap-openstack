# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import click
import yaml
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    read_config,
    run_plan,
    str_presenter,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.steps.juju import RemoveSaasApplicationsStep
from sunbeam.steps.sso import (
    APPLICATION_REMOVE_TIMEOUT,
    SSO_CONFIG_KEY,
    AddCanonicalProviderStep,
    AddEntraProviderStep,
    AddGenericProviderStep,
    AddGoogleProviderStep,
    AddOktaProviderStep,
    RemoveExternalProviderStep,
    UpdateExternalProviderStep,
)
from sunbeam.utils import click_option_show_hints

console = Console()


@click.command(name="list")
@click.pass_context
@click_option_show_hints
@click.option(
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format",
)
def list_sso(
    ctx: click.Context,
    format: str,
    show_hints: bool = False,
) -> None:
    """List SSO providers."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {}

    results = {}
    for k, v in cfg.items():
        results[k] = {
            "type": v.get("provider_type", "unknown"),
            "protocol": v.get("provider_proto", "unknown"),
            "issuer_url": v.get("config", {}).get("issuer_url", "unknown"),
        }

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Provider ID")
        table.add_column("Type")
        table.add_column("Protocol")
        for provider, data in results.items():
            table.add_row(
                provider,
                data["type"],
                data["protocol"],
            )
        console.print(table)
    elif format == FORMAT_YAML:
        yaml.add_representer(str, str_presenter)
        console.print(yaml.dump(results))


@click.command(name="add")
@click.argument(
    "provider-type",
    type=click.Choice(
        ["canonical", "google", "entra", "okta", "generic"],
        case_sensitive=False,
    ),
)
@click.argument(
    "provider-protocol",
    type=click.Choice(
        [
            "openid",
        ],
        case_sensitive=False,
    ),
)
@click.argument("name", type=str)
@click.option(
    "--config",
    type=str,
    required=False,
    help="SSO provider configuration",
)
@click_option_show_hints
@click.pass_context
def add_sso(
    ctx: click.Context,
    provider_type: str,
    provider_protocol: str,
    name: str,
    config: str,
    show_hints: bool,
) -> None:
    """Add a new SSO Provider."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {}

    if name in cfg:
        click.echo(f"{name} is already enabled.")
        return

    jhelper = JujuHelper(deployment.juju_controller)

    step_map = {
        "google": AddGoogleProviderStep,
        "entra": AddEntraProviderStep,
        "okta": AddOktaProviderStep,
        "generic": AddGenericProviderStep,
        "canonical": AddCanonicalProviderStep,
    }

    step_cls = step_map.get(provider_type)
    if not step_cls:
        raise click.ClickException(f"Cannot handle {provider_type}")

    charm_config: dict[str, str] = {}
    try:
        with open(config) as fd:
            charm_config = yaml.safe_load(fd)
    except Exception as err:
        raise click.ClickException(f"Invalid config supplied: {err}")

    step = step_cls(
        deployment,
        jhelper,
        provider_protocol,
        name,
        charm_config,
    )

    plan = [
        TerraformInitStep(deployment.get_tfhelper("openstack-plan")),
        step,
    ]
    run_plan(plan, console, show_hints)
    click.echo(f"{name} added.")


@click.command(name="remove")
@click.argument("name", type=str)
@click.option(
    "--yes-i-mean-it",
    is_flag=True,
    help="Do not prompt for confirmation.",
)
@click_option_show_hints
@click.pass_context
def remove_sso(ctx: click.Context, name: str, yes_i_mean_it: bool, show_hints: bool):
    """Remove a SSO provider."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {}

    provider = cfg.get(name)
    if not provider:
        click.echo(f"{name} does not exist.")
        return

    if not yes_i_mean_it:
        msg = f"This action will disable {name}. Are you sure?"
        click.confirm(msg, abort=True)

    jhelper = JujuHelper(deployment.juju_controller)
    plan: list[BaseStep] = [
        TerraformInitStep(deployment.get_tfhelper("openstack-plan")),
    ]
    prov_type = provider.get("provider_type", None)
    if prov_type == "canonical":
        plan.append(
            RemoveSaasApplicationsStep(
                jhelper,
                OPENSTACK_MODEL,
                saas_apps_to_delete=[name, f"{name}-cert"],
                offering_interfaces=["oauth", "certificate_transfer"],
                wait_timeout=APPLICATION_REMOVE_TIMEOUT,
            )
        )
    else:
        plan.append(
            RemoveExternalProviderStep(
                deployment=deployment,
                jhelper=jhelper,
                provider_name=name,
            )
        )

    run_plan(plan, console, show_hints)
    if prov_type == "canonical":
        del cfg[name]
        update_config(deployment.get_client(), SSO_CONFIG_KEY, cfg)
    click.echo(f"{name} removed.")


@click.command(name="update")
@click.argument("name", type=str)
@click.option(
    "--secrets-file",
    type=str,
    required=True,
    help="Secrets file containing client_id and client_secret",
)
@click_option_show_hints
@click.pass_context
def update_sso(ctx: click.Context, name: str, secrets_file: str, show_hints: bool):
    """Update external provider client secrets."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {}

    if name not in cfg:
        click.echo(f"{name} does not exist.")
        return

    secrets: dict[str, str] = {}
    try:
        with open(secrets_file) as fd:
            secrets = yaml.safe_load(fd)
    except Exception as e:
        raise click.ClickException(f"Invalid config supplied: {e}")

    jhelper = JujuHelper(deployment.juju_controller)
    plan = [
        TerraformInitStep(deployment.get_tfhelper("openstack-plan")),
        UpdateExternalProviderStep(
            deployment=deployment,
            jhelper=jhelper,
            provider_name=name,
            secrets=secrets,
        ),
    ]
    run_plan(plan, console, show_hints)
    click.echo(f"{name} updated.")


@click.command(name="get-oidc-redirect-url")
@click.pass_context
def get_openid_redirect_uri(ctx: click.Context):
    """Get the OpenID redirect URI."""
    deployment: Deployment = ctx.obj
    jhelper = JujuHelper(deployment.juju_controller)
    app = "keystone"
    action_cmd = "get-admin-account"

    try:
        unit = jhelper.get_leader_unit(app, OPENSTACK_MODEL)
    except LeaderNotFoundException:
        raise click.ClickException(f"Unable to get {app} leader")

    try:
        action_result = jhelper.run_action(unit, OPENSTACK_MODEL, action_cmd)
    except ActionFailedException:
        raise click.ClickException(
            "Unable to retrieve admin account data from Keystone service"
        )
    public_url = action_result.get("public-endpoint", "").rstrip("/")
    if not public_url:
        raise click.ClickException("Could not determine keystone public URL")
    redirect_uri = f"{public_url}/OS-FEDERATION/protocols/openid/redirect_uri"
    click.echo(f"{redirect_uri}")


@click.command(name="purge")
@click_option_show_hints
@click.option(
    "--yes-i-mean-it",
    is_flag=True,
    help="Do not prompt for confirmation.",
)
@click.pass_context
def purge_sso(
    ctx: click.Context,
    show_hints: bool,
    yes_i_mean_it: bool,
) -> None:
    """Remove all SSO providers."""
    deployment: Deployment = ctx.obj
    jhelper = JujuHelper(deployment.juju_controller)
    client = deployment.get_client()
    try:
        config = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        config = {}

    if not yes_i_mean_it and config:
        msg = (
            "You have one or more SSO providers enabled. "
            "This action will disable all of them. Are you sure?"
        )
        click.confirm(msg, abort=True)

    tfhelper = deployment.get_tfhelper("openstack-plan")
    remove_idp_plan: list[BaseStep] = [
        TerraformInitStep(tfhelper),
    ]
    saas_to_remove = []
    for provider, cfg in config.items():
        if cfg.get("provider_type", None) == "canonical":
            saas_to_remove.append(provider)
            saas_to_remove.append(f"{provider}-cert")
        else:
            remove_idp_plan.append(
                RemoveExternalProviderStep(
                    deployment=deployment,
                    jhelper=jhelper,
                    provider_name=provider,
                )
            )

    if saas_to_remove:
        remove_idp_plan.append(
            RemoveSaasApplicationsStep(
                jhelper,
                OPENSTACK_MODEL,
                saas_apps_to_delete=saas_to_remove,
                offering_interfaces=["oauth", "certificate_transfer"],
                wait_timeout=APPLICATION_REMOVE_TIMEOUT,
            ),
        )
    run_plan(remove_idp_plan, console, show_hints)
    update_config(client, SSO_CONFIG_KEY, {})
