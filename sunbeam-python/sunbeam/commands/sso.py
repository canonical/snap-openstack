# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
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
from sunbeam.steps.juju import JujuLoginStep, RemoveSaasApplicationsStep
from sunbeam.steps.sso import (
    APPLICATION_REMOVE_TIMEOUT,
    SSO_CONFIG_KEY,
    VALID_SSO_PROTOCOLS,
    AddCanonicalProviderStep,
    AddEntraProviderStep,
    AddGenericProviderStep,
    AddGoogleProviderStep,
    AddOktaProviderStep,
    RemoveExternalProviderStep,
    SetKeystoneSAMLCertAndKeyStep,
    UpdateExternalProviderStep,
    safe_get_sso_config,
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
    """List identity providers."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    cfg = safe_get_sso_config(client)
    results: dict[str, dict[str, Any]] = {
        "openid": {},
        "saml2": {},
    }

    for proto, providers in cfg.items():
        for provider, data in providers.items():
            results[proto][provider] = {
                "type": data.get("provider_type", "unknown"),
                "protocol": proto,
            }
            if proto == "openid":
                results[proto][provider]["remote_id"] = data.get("config", {}).get(
                    "issuer_url", "unknown"
                )
            elif proto == "saml2":
                results[proto][provider]["remote_id"] = data.get(
                    "remote_entity_id", "unknown"
                )

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Name")
        table.add_column("Provider")
        table.add_column("Protocol")
        table.add_column("Remote ID")
        table.add_row(
            "Keystone Credentials",
            "Built-in",
            "keystone",
            "N/A",
        )
        for proto, providers in results.items():
            for provider, data in providers.items():
                table.add_row(
                    provider,
                    data["type"],
                    data["protocol"],
                    data["remote_id"],
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
        VALID_SSO_PROTOCOLS,
        case_sensitive=False,
    ),
)
@click.argument("name", type=str)
@click.option(
    "--config",
    type=str,
    required=False,
    help="Identity provider configuration",
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
    """Add a new identity provider."""
    deployment: Deployment = ctx.obj

    client = deployment.get_client()
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)

    cfg = safe_get_sso_config(client)
    if cfg.get(provider_protocol, {}).get(name, {}):
        click.echo(f"{name} ({provider_protocol}) is already enabled.")
        return

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

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
@click.argument(
    "protocol",
    type=click.Choice(
        VALID_SSO_PROTOCOLS,
        case_sensitive=False,
    ),
)
@click.option(
    "--yes-i-mean-it",
    is_flag=True,
    help="Do not prompt for confirmation.",
)
@click_option_show_hints
@click.pass_context
def remove_sso(
    ctx: click.Context,
    name: str,
    protocol: str,
    yes_i_mean_it: bool,
    show_hints: bool,
):
    """Remove an identity provider."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)
    cfg = safe_get_sso_config(client)

    provider = cfg.get(protocol, {}).get(name)
    if not provider:
        click.echo(f"{name} does not exist.")
        return

    if not yes_i_mean_it:
        msg = f"This action will remove {name}. Are you sure?"
        click.confirm(msg, abort=True)

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

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
                provider_proto=protocol,
            )
        )

    run_plan(plan, console, show_hints)
    if prov_type == "canonical":
        del cfg[name]
        update_config(deployment.get_client(), SSO_CONFIG_KEY, cfg)
    click.echo(f"{name} removed.")


@click.command(name="update")
@click.argument("name", type=str)
@click.argument(
    "protocol",
    type=click.Choice(
        VALID_SSO_PROTOCOLS,
        case_sensitive=False,
    ),
)
@click.option(
    "--secrets-file",
    type=str,
    required=True,
    help="Secrets file containing client_id and client_secret",
)
@click_option_show_hints
@click.pass_context
def update_sso(
    ctx: click.Context, name: str, protocol: str, secrets_file: str, show_hints: bool
):
    """Update identity provider (openid only)."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)
    try:
        cfg = read_config(client, SSO_CONFIG_KEY)
    except ConfigItemNotFoundException:
        cfg = {
            "openid": {},
            "saml2": {},
        }

    if name not in cfg.get(protocol, {}):
        click.echo(f"{name} with protocol {protocol} does not exist.")
        return

    secrets: dict[str, str] = {}
    try:
        with open(secrets_file) as fd:
            secrets = yaml.safe_load(fd)
    except Exception as e:
        raise click.ClickException(f"Invalid config supplied: {e}")

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

    jhelper = JujuHelper(deployment.juju_controller)

    plan = [
        TerraformInitStep(deployment.get_tfhelper("openstack-plan")),
        UpdateExternalProviderStep(
            deployment=deployment,
            jhelper=jhelper,
            provider_name=name,
            provider_proto=protocol,
            secrets=secrets,
        ),
    ]
    run_plan(plan, console, show_hints)
    click.echo(f"{name} ({protocol}) updated.")


@click.command(name="get-oidc-redirect-url")
@click.pass_context
def get_openid_redirect_uri(ctx: click.Context):
    """Get the OpenID redirect URI."""
    deployment: Deployment = ctx.obj

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console)

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
    """Remove all identity providers."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

    jhelper = JujuHelper(deployment.juju_controller)

    config = safe_get_sso_config(client)
    if not yes_i_mean_it and any(config.values()):
        msg = (
            "You have one or more identity providers enabled. "
            "This action will remove all of them. Are you sure?"
        )
        click.confirm(msg, abort=True)

    tfhelper = deployment.get_tfhelper("openstack-plan")
    remove_idp_plan: list[BaseStep] = [
        TerraformInitStep(tfhelper),
    ]
    saas_to_remove = []
    for proto, section in config.items():
        for provider, cfg in section.items():
            if cfg.get("provider_type", None) == "canonical":
                saas_to_remove.append(provider)
                saas_to_remove.append(f"{provider}-cert")
            else:
                remove_idp_plan.append(
                    RemoveExternalProviderStep(
                        deployment=deployment,
                        jhelper=jhelper,
                        provider_name=provider,
                        provider_proto=proto,
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


@click.command(name="set-saml-x509")
@click_option_show_hints
@click.argument("certificate", type=str)
@click.argument("key", type=str)
@click.pass_context
def set_saml_x509(
    ctx: click.Context,
    show_hints: bool,
    certificate: str,
    key: str,
) -> None:
    """Set Keystone SAML x509 SP certificate and key."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    tfhelper = deployment.get_tfhelper("openstack-plan")
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console, show_hints)

    jhelper = JujuHelper(deployment.juju_controller)

    run_plan(
        [
            TerraformInitStep(deployment.get_tfhelper("openstack-plan")),
            SetKeystoneSAMLCertAndKeyStep(
                deployment,
                tfhelper,
                jhelper,
                None,
                certificate,
                key,
            ),
        ],
        console,
        show_hints,
    )
