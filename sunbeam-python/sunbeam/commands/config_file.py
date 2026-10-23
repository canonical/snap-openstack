# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

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
    validate_overrides,
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
    """Read the config (and policy) files, leaving their content unchecked.

    The unit validates the content before anything is attached, so a file
    that is not a usable override is reported by the service itself.
    """
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
    policy_text = None
    if policy is not None:
        try:
            policy_text = policy.read_text()
        except OSError as e:
            raise click.ClickException(
                f"unable to read policy file {policy}: {e}"
            ) from e
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
@click_option_show_hints
@click.pass_context
@_handle_errors
def add(
    ctx: click.Context,
    app: str,
    file: Path | None,
    policy: Path | None,
    show_hints: bool,
) -> None:
    """Store a config file override for an application and apply it.

    At least one of the config file and the policy file is required, so a
    policy-only override is allowed.  The content is validated on the
    deployed service first: content the service rejects is stored but never
    attached, so it can be inspected and fixed.  Valid content is attached
    as the charm's config-override/policy-override juju resources, replacing
    any previous override completely (a missing half is cleared with an
    empty file), and applied via the charm's apply-config-overrides action.
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
    config_text = record.get("config")
    policy_text = record.get("policy")
    check_charm_support(jhelper, app, policy=policy_text is not None)
    if not _validate_unit(jhelper, app, config_text, policy_text):
        raise ConfigOverridesError(
            f"Validation failed for {app}; override not applied."
        )
    attach_overrides(jhelper, app, config_text, policy_text)
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


def _print_override(app: str, record: dict, *, header: bool = False) -> None:
    """Print the stored halves of one override."""
    config_text = record.get("config")
    policy_text = record.get("policy")
    if config_text is not None:
        if header:
            console.print(f"# --- {app} ---")
        console.print(config_text, markup=False, highlight=False)
    if policy_text is not None:
        console.print(f"# --- {app} policy ---" if header else "# --- policy ---")
        console.print(policy_text, markup=False, highlight=False)


def _write_override(output: Path, record: dict) -> None:
    """Write the stored halves of one override next to ``output``."""
    try:
        if record.get("config") is not None:
            output.write_text(record["config"])
            console.print(f"Wrote config to {output}")
        if record.get("policy") is not None:
            policy_output = output.with_name(output.name + ".policy.yaml")
            policy_output.write_text(record["policy"])
            console.print(f"Wrote policy to {policy_output}")
    except OSError as e:
        raise click.ClickException(f"unable to write {output}: {e}") from e


def _write_overrides(output_dir: Path, overrides: dict) -> int:
    """Write every stored override into ``output_dir``, returning the count."""

    def _write(path: Path, text: str) -> None:
        try:
            path.write_text(text)
        except OSError as e:
            raise click.ClickException(f"unable to write {path}: {e}") from e

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise click.ClickException(f"unable to create {output_dir}: {e}") from e
    written = 0
    for app in sorted(overrides):
        record = overrides[app]
        if record.get("config") is not None:
            _write(output_dir / f"{app}.conf", record["config"])
            written += 1
        if record.get("policy") is not None:
            _write(output_dir / f"{app}.policy.yaml", record["policy"])
            written += 1
    return written


@override.command("get")
@click.argument("app", type=str, required=False)
@click.option(
    "--all",
    "all_apps",
    is_flag=True,
    default=False,
    help="Print every stored override.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write one application's stored files to this path.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write every stored override into this directory (with --all).",
)
@click.pass_context
@_handle_errors
def get(
    ctx: click.Context,
    app: str | None,
    all_apps: bool,
    output: Path | None,
    output_dir: Path | None,
) -> None:
    """Print the stored config file override of an application.

    With --all every stored override is printed, or written into --output-dir
    when given, for support and debugging.  For one application, --output
    writes the files next to the given path instead of printing them;
    otherwise the unit is asked which override files it has applied.
    """
    if app is not None and all_apps:
        raise click.UsageError("pass an application or --all, not both")
    if output is not None and all_apps:
        raise click.UsageError("--output writes one application, use --output-dir")
    if output_dir is not None and not all_apps:
        raise click.UsageError("--output-dir needs --all")

    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    if all_apps:
        overrides = read_overrides(client)
        if not overrides:
            console.print("No config-file overrides stored.")
            return
        if output_dir is None:
            for name in sorted(overrides):
                _print_override(name, overrides[name], header=True)
            return
        written = _write_overrides(output_dir, overrides)
        console.print(f"Collected {written} override(s) into {output_dir}.")
        return

    if app is None:
        raise click.UsageError("pass an application or --all")
    record = get_override(client, app)
    if output is not None:
        _write_override(output, record)
        return
    _print_override(app, record)
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
