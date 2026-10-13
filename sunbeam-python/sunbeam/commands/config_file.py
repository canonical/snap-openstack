# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import datetime
import functools
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Column, Table

from sunbeam.core.checks import JujuLoginCheck, run_preflight_checks
from sunbeam.core.common import CONTEXT_SETTINGS
from sunbeam.core.config_overrides import (
    ConfigOverridesError,
    ValidationResult,
    apply_overrides,
    attach_overrides,
    check_charm_support,
    get_applied_overrides,
    get_override,
    make_record,
    read_overrides,
    remove_override,
    set_override,
    validate_ini,
    validate_overrides,
    validate_policy_yaml,
    wait_for_settle,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuException, JujuHelper
from sunbeam.utils import CatchGroup, click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()


def _handle_errors(func):
    """Turn config-override/juju errors into clean click errors."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ConfigOverridesError, JujuException) as e:
            raise click.ClickException(str(e)) from e

    return wrapper


def _jhelper(deployment: Deployment) -> JujuHelper:
    return JujuHelper(deployment.juju_controller)


def _preflight_checks(deployment: Deployment) -> None:
    run_preflight_checks([JujuLoginCheck(deployment.juju_account)], console)


def _read_local_files(
    file: Path | None, policy: Path | None
) -> tuple[str | None, str | None]:
    """Read and locally validate the config (and policy) files."""
    if file is None and policy is None:
        raise click.ClickException(
            "provide a configuration file, a policy file, or both"
        )
    config_text = None
    if file is not None:
        try:
            config_text = file.read_text()
        except OSError as e:
            raise click.ClickException(f"unable to read config file {file}: {e}") from e
        validate_ini(config_text)
    policy_text = None
    if policy is not None:
        try:
            policy_text = policy.read_text()
        except OSError as e:
            raise click.ClickException(
                f"unable to read policy file {policy}: {e}"
            ) from e
        validate_policy_yaml(policy_text)
    return config_text, policy_text


def _print_validation(result: ValidationResult) -> None:
    for error in result.errors:
        console.print(f"[red]error:[/red] {error}")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for name, check in result.checks.items():
        console.print(f"check {name}: {check}")
    if result.valid:
        console.print("[green]Validation succeeded.[/green]")


def _validate_unit(
    jhelper: JujuHelper,
    app: str,
    config_text: str | None,
    policy_text: str | None,
) -> bool:
    """Validate each override file on the unit; True when all are valid."""
    valid = True
    for content in (config_text, policy_text):
        if content is None:
            continue
        result = validate_overrides(jhelper, app, content)
        _print_validation(result)
        valid = valid and result.valid
    return valid


def _enable(
    deployment: Deployment, jhelper: JujuHelper, app: str, record: dict
) -> None:
    """Apply an attached override and mark the stored record as enabled."""
    result = apply_overrides(jhelper, app)
    _print_validation(result)
    if not result.valid:
        raise ConfigOverridesError(
            f"Validation failed for {app}; override not applied."
        )
    record["enabled"] = True
    set_override(deployment.get_client(), app, record)
    console.print(f"Enabled config-file override for {app}.")
    if not wait_for_settle(jhelper, app):
        console.print(
            f"[yellow]warning:[/yellow] application {app} did not settle; "
            f"check `juju status {app}`."
        )


def _detach(jhelper: JujuHelper, app: str) -> None:
    """Clear the attached override resources and let the charm drop them."""
    attach_overrides(jhelper, app, None, None)
    apply_overrides(jhelper, app)


@click.group("config-file", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def config_file(ctx: click.Context) -> None:
    """Manage config-file overrides for OpenStack services."""


@config_file.group("override", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def override(ctx: click.Context) -> None:
    """Manage per-service config-file overrides."""


@override.command("add")
@click.argument("app", type=str)
@click.argument(
    "file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--policy",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Policy YAML file with the override rules.",
)
@click.option("--enable", is_flag=True, default=False, help="Apply it immediately.")
@click_option_show_hints
@click.pass_context
@_handle_errors
def add(
    ctx: click.Context,
    app: str,
    file: Path | None,
    policy: Path | None,
    enable: bool,
    show_hints: bool,
) -> None:
    """Store a config file override for an application.

    The config file and the policy file are validated locally and against
    the deployed service before anything is stored or attached; at least one
    of them is required, so a policy-only override is allowed. The files are
    attached as the charm's config-override/policy-override juju resources,
    replacing any previous override completely (a missing half is cleared
    with an empty file). With --enable the override is applied immediately
    via the charm's apply-config-overrides action.
    """
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    config_text, policy_text = _read_local_files(file, policy)
    record = make_record(config_text, policy_text)

    _preflight_checks(deployment)
    jhelper = _jhelper(deployment)
    check_charm_support(jhelper, app, policy=policy_text is not None)

    set_override(client, app, record)
    console.print(
        f"Stored config-file override for {app} (sha256 {record['sha256'][:12]})."
    )

    if not _validate_unit(jhelper, app, config_text, policy_text):
        raise ConfigOverridesError(
            f"Validation failed for {app}; override was stored but not applied."
        )

    attach_overrides(jhelper, app, config_text, policy_text)

    if not enable:
        console.print(f"Run `sunbeam config-file override enable {app}` to apply it.")
        return

    _enable(deployment, jhelper, app, record)


@override.command("enable")
@click.argument("app", type=str)
@click_option_show_hints
@click.pass_context
@_handle_errors
def enable(ctx: click.Context, app: str, show_hints: bool) -> None:
    """Attach the stored override and apply it to the service."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    record = get_override(client, app)
    _preflight_checks(deployment)
    jhelper = _jhelper(deployment)
    attach_overrides(jhelper, app, record.get("config"), record.get("policy"))
    _enable(deployment, jhelper, app, record)


@override.command("disable")
@click.argument("app", type=str)
@click_option_show_hints
@click.pass_context
@_handle_errors
def disable(ctx: click.Context, app: str, show_hints: bool) -> None:
    """Disable a config-file override, keeping the stored content."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    record = get_override(client, app)
    _preflight_checks(deployment)
    _detach(_jhelper(deployment), app)
    record["enabled"] = False
    set_override(client, app, record)
    console.print(
        f"Disabled config-file override for {app} (the stored content is kept)."
    )


@override.command("remove")
@click.argument("app", type=str)
@click.option("--force", is_flag=True, default=False)
@click_option_show_hints
@click.pass_context
@_handle_errors
def remove(ctx: click.Context, app: str, force: bool, show_hints: bool) -> None:
    """Drop the stored override and clear the attached resources."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    record = get_override(client, app)
    if record.get("enabled") and not force:
        raise click.ClickException(
            f"config-file override for {app!r} is enabled; disable it first "
            "or pass --force"
        )
    _preflight_checks(deployment)
    _detach(_jhelper(deployment), app)
    remove_override(client, app)
    console.print(f"Removed config-file override for {app}.")


@override.command("get")
@click.argument("app", type=str)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the stored config file to this path.",
)
@click.pass_context
@_handle_errors
def get(ctx: click.Context, app: str, output: Path | None) -> None:
    """Print the stored config file override for an application."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    record = get_override(client, app)
    config_text = record.get("config")
    policy_text = record.get("policy")
    if output is not None:
        if config_text is not None:
            output.write_text(config_text)
            console.print(f"Wrote config to {output}")
        if policy_text is not None:
            policy_output = output.with_name(output.name + ".policy.yaml")
            policy_output.write_text(policy_text)
            console.print(f"Wrote policy to {policy_output}")
    else:
        if config_text is not None:
            console.print(config_text, markup=False, highlight=False)
        if policy_text is not None:
            console.print("# --- policy ---")
            console.print(policy_text, markup=False, highlight=False)
    try:
        applied = get_applied_overrides(_jhelper(deployment), app)
    except (ConfigOverridesError, JujuException) as e:
        LOG.debug("Could not fetch applied override for %s: %r", app, e)
        return
    console.print(f"Applied on the unit: {', '.join(applied) or 'none'}")


@override.command("list")
@click.pass_context
@_handle_errors
def list_overrides(ctx: click.Context) -> None:
    """List stored config file overrides."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    overrides = read_overrides(client)
    if not overrides:
        console.print("No config-file overrides stored.")
        return
    table = Table(
        Column("Application"),
        Column("Enabled"),
        Column("sha256"),
        Column("Added at"),
        title="Config-file overrides",
    )
    for app in sorted(overrides):
        record = overrides[app]
        table.add_row(
            app,
            "yes" if record.get("enabled") else "no",
            str(record.get("sha256", ""))[:12],
            str(record.get("added_at", "")),
        )
    console.print(table)


@override.command("validate")
@click.argument("app", type=str)
@click.argument(
    "file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--policy",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Policy YAML file with the override rules.",
)
@click.pass_context
@_handle_errors
def validate(
    ctx: click.Context, app: str, file: Path | None, policy: Path | None
) -> None:
    """Validate a config file override on the unit without applying it."""
    deployment: Deployment = ctx.obj
    config_text, policy_text = _read_local_files(file, policy)
    _preflight_checks(deployment)
    jhelper = _jhelper(deployment)
    if policy_text is not None:
        check_charm_support(jhelper, app, policy=True)
    if not _validate_unit(jhelper, app, config_text, policy_text):
        raise ConfigOverridesError(f"Validation failed for {app}.")


@override.command("collect")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to write the collected overrides to.",
)
@click.pass_context
@_handle_errors
def collect(ctx: click.Context, output_dir: Path | None) -> None:
    """Dump every stored override to files for support/debugging."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    overrides = read_overrides(client)
    if output_dir is None:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        output_dir = Path.cwd() / f"sunbeam-config-overrides-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for app in sorted(overrides):
        record = overrides[app]
        config_text = record.get("config")
        policy_text = record.get("policy")
        if config_text is not None:
            (output_dir / f"{app}.conf").write_text(config_text)
            written += 1
        if policy_text is not None:
            (output_dir / f"{app}.policy.yaml").write_text(policy_text)
            written += 1
    console.print(f"Collected {written} override(s) into {output_dir}.")
