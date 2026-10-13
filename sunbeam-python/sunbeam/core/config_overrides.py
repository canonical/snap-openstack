# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Helpers to manage per-service config-file overrides.

Config-file overrides let operators inject an INI config file (and an optional
oslo.policy YAML) into an OpenStack service charm. The files travel as juju
file resources (``config-override`` / ``policy-override``) attached with
``juju attach-resource``; an attached resource can never be detached, so an
EMPTY file means "no override". The content is also stored in the cluster
database because a resource's bytes cannot be downloaded back through juju.
"""

import configparser
import datetime
import hashlib
import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jubilant
import yaml

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import read_config, update_config
from sunbeam.core.juju import (
    CONFIG_OVERRIDE_FILENAME,
    CONFIG_OVERRIDE_RESOURCE,
    POLICY_OVERRIDE_FILENAME,
    POLICY_OVERRIDE_RESOURCE,
    ActionFailedException,
    JujuException,
    JujuHelper,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.errors import SunbeamException

if TYPE_CHECKING:
    from rich.console import Console

    from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)

# Cluster database config item holding the stored overrides for the deployment.
CONFIG_FILE_OVERRIDES_KEY = "ConfigFileOverrides"

# Charm actions used to validate/apply/fetch overrides on a deployed unit.
VALIDATE_OVERRIDES_ACTION = "validate-config-overrides"
APPLY_OVERRIDES_ACTION = "apply-config-overrides"
GET_OVERRIDES_ACTION = "get-config-overrides"

# Seconds to wait for a unit to settle after a resource change before
# retrying an action that the unit agent restart interrupted.
APPLY_SETTLE_TIMEOUT = 300

# Attempts for the apply action.  Attaching a resource recreates the unit
# pod, and juju kills any task that was in flight when the pod was replaced,
# so an attempt can fail without saying anything about the override itself.
APPLY_ATTEMPTS = 3


class ConfigOverridesError(SunbeamException):
    """Raised for any config-file override related error."""


@dataclass
class ValidationResult:
    """Parsed result of an override charm action."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)


def validate_ini(text: str) -> None:
    """Strictly validate INI text, raising on syntax/duplicate issues."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.DuplicateSectionError as e:
        raise ConfigOverridesError(
            f"duplicate section {e.section!r} in config file"
        ) from e
    except configparser.DuplicateOptionError as e:
        raise ConfigOverridesError(
            f"duplicate option {e.option!r} in section {e.section!r} of config file"
        ) from e
    except configparser.MissingSectionHeaderError as e:
        raise ConfigOverridesError(
            f"config file is missing a section header before line {e.lineno}: "
            f"{e.line!r}"
        ) from e
    except configparser.Error as e:
        raise ConfigOverridesError(f"invalid INI config file: {e}") from e


def validate_policy_yaml(text: str) -> None:
    """Validate that the policy text is syntactically valid YAML mapping."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigOverridesError(f"invalid policy YAML file: {e}") from e
    if data is not None and not isinstance(data, dict):
        raise ConfigOverridesError("policy YAML file must contain a mapping")


def content_sha256(config: str | None, policy: str | None) -> str:
    """Return a stable hex digest of the stored override content."""
    digest = hashlib.sha256()
    for text in (config, policy):
        digest.update((text or "").encode())
        digest.update(b"\0")
    return digest.hexdigest()


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_overrides(client) -> dict[str, dict]:
    """Read the stored overrides, returning an empty mapping when absent."""
    try:
        data = read_config(client, CONFIG_FILE_OVERRIDES_KEY)
    except ConfigItemNotFoundException:
        return {}
    overrides = data.get("overrides", {})
    if not isinstance(overrides, dict):
        LOG.warning("Malformed %s config item, ignoring", CONFIG_FILE_OVERRIDES_KEY)
        return {}
    return overrides


def write_overrides(client, overrides: dict[str, dict]) -> None:
    """Persist the overrides to the cluster database."""
    update_config(client, CONFIG_FILE_OVERRIDES_KEY, {"overrides": overrides})


def get_override(client, app: str) -> dict:
    """Return the stored record for an application, raising when absent."""
    overrides = read_overrides(client)
    if app not in overrides:
        raise ConfigOverridesError(
            f"no config-file override stored for application {app!r}"
        )
    return overrides[app]


def set_override(client, app: str, record: dict) -> None:
    """Store (or replace) the record for an application."""
    overrides = read_overrides(client)
    overrides[app] = record
    write_overrides(client, overrides)


def remove_override(client, app: str) -> None:
    """Delete the stored record for an application."""
    overrides = read_overrides(client)
    if app not in overrides:
        raise ConfigOverridesError(
            f"no config-file override stored for application {app!r}"
        )
    del overrides[app]
    write_overrides(client, overrides)


def make_record(config: str | None, policy: str | None) -> dict:
    """Build a new (disabled) store record for an application."""
    return {
        "config": config,
        "policy": policy,
        "sha256": content_sha256(config, policy),
        "enabled": False,
        "added_at": now_iso(),
    }


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _parse_json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [str(value)] if value else []
    return parsed if isinstance(parsed, list) else [parsed]


def _parse_json_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_validation_result(results: dict) -> ValidationResult:
    """Parse the action results into a :class:`ValidationResult`."""
    return ValidationResult(
        valid=_coerce_bool(results.get("valid")),
        errors=_parse_json_list(results.get("errors")),
        warnings=_parse_json_list(results.get("warnings")),
        checks=_parse_json_dict(results.get("checks")),
    )


def _declared_resources(jhelper: JujuHelper, app: str, model: str) -> set[str]:
    try:
        resources = jhelper.get_application_resources(app, model)
    except (JujuException, jubilant.CLIError) as e:
        raise ConfigOverridesError(
            f"application {app!r} is not deployed in model {model!r}"
        ) from e
    return {str(r.get("name", "")) for r in resources}


def _require_resource(resources: set[str], app: str, resource: str) -> None:
    if resource not in resources:
        raise ConfigOverridesError(
            f"charm deployed for application {app!r} does not declare the "
            f"{resource!r} resource; refresh to a charm revision that "
            "supports config-file overrides"
        )


def check_charm_support(
    jhelper: JujuHelper,
    app: str,
    policy: bool = False,
    model: str = OPENSTACK_MODEL,
) -> None:
    """Ensure the deployed charm declares the override resources.

    :param policy: also require the policy override resource.
    """
    resources = _declared_resources(jhelper, app, model)
    _require_resource(resources, app, CONFIG_OVERRIDE_RESOURCE)
    if policy:
        _require_resource(resources, app, POLICY_OVERRIDE_RESOURCE)


def attach_overrides(
    jhelper: JujuHelper,
    app: str,
    config: str | None,
    policy: str | None,
    model: str = OPENSTACK_MODEL,
) -> None:
    """Attach the override content to the application's juju resources.

    The whole override is replaced: a half left out is attached as an EMPTY
    file, which the charm reads as "no override". The policy resource is only
    used when the charm declares it.
    """
    resources = _declared_resources(jhelper, app, model)
    _require_resource(resources, app, CONFIG_OVERRIDE_RESOURCE)
    if policy is not None:
        _require_resource(resources, app, POLICY_OVERRIDE_RESOURCE)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        def _attach(resource: str, filename: str, text: str | None) -> None:
            path = tmpdir / filename
            path.write_text(text or "")
            jhelper.attach_resource(app, model, resource, str(path))

        _attach(CONFIG_OVERRIDE_RESOURCE, CONFIG_OVERRIDE_FILENAME, config)
        if policy is not None or POLICY_OVERRIDE_RESOURCE in resources:
            _attach(POLICY_OVERRIDE_RESOURCE, POLICY_OVERRIDE_FILENAME, policy)


def validate_overrides(
    jhelper: JujuHelper,
    app: str,
    content: str,
    model: str = OPENSTACK_MODEL,
) -> ValidationResult:
    """Validate one override file's raw text on the application leader unit."""
    check_charm_support(jhelper, app, model=model)
    unit = jhelper.get_leader_unit(app, model)
    try:
        results = jhelper.run_action(
            unit, model, VALIDATE_OVERRIDES_ACTION, {"content": content}
        )
    except ActionFailedException as e:
        if "valid" not in e.results:
            raise
        # The charm fails the action for a payload the service rejects; the
        # results still hold the verdict, which is not a run failure.
        return parse_validation_result(e.results)
    return parse_validation_result(results)


def _looks_transient(exc: ActionFailedException) -> bool:
    """Whether an action failure is the unit restarting, not a verdict.

    Two observed shapes: juju kills the task when the unit agent is replaced
    (``return code -1 ... Message: terminated``), and the charm raises while
    the workload container comes back (``Uncaught ConnectionError in charm
    code: Could not connect to Pebble: socket not found at
    '/charm/containers/<service>/pebble.socket' (container restarted?)``).
    Only the wording of the final error depends on this; the retry itself
    covers every failed task, so an unknown shape is still retried and just
    reported verbatim.
    """
    text = str(exc)
    return any(
        marker in text
        for marker in (
            "terminated",
            "container restarted",
            "Could not connect to Pebble",
        )
    )


def wait_for_settle(
    jhelper: JujuHelper, app: str, model: str = OPENSTACK_MODEL
) -> bool:
    """Wait for the application to react to a resource change.

    A resource change recreates the unit pod; the unit is only ready for
    another action once the agent is back and the charm finished the
    config-changed it triggered (active, or blocked on a bad override).

    :returns: whether the application settled before the timeout.
    """
    try:
        jhelper.wait_until_desired_status(
            model,
            [app],
            status=["active", "blocked"],
            agent_status=["idle"],
            timeout=APPLY_SETTLE_TIMEOUT,
        )
    except (JujuWaitException, TimeoutError) as e:
        LOG.debug("application %s did not settle: %s", app, e)
        return False
    return True


def apply_overrides(
    jhelper: JujuHelper,
    app: str,
    model: str = OPENSTACK_MODEL,
) -> ValidationResult:
    """Apply the attached override resources via the charm's apply action.

    Attaching a resource recreates the unit pod and restarts the workload, so
    an action started right after the attach can fail without judging the
    override at all.  A failed task carries no verdict, and the action is
    idempotent, so it is retried after the application settles (up to
    ``APPLY_ATTEMPTS``): an override the service rejects comes back as a
    result (``valid: false``) and is never retried, while a deterministic
    failure fails the same way and is reported from the last attempt.
    """
    check_charm_support(jhelper, app, model=model)
    unit = jhelper.get_leader_unit(app, model)
    attempt = 1
    while True:
        try:
            results = jhelper.run_action(unit, model, APPLY_OVERRIDES_ACTION, {})
        except ActionFailedException as e:
            if "valid" in e.results:
                # A verdict from the service, not a failed run: never retried.
                return parse_validation_result(e.results)
            if attempt >= APPLY_ATTEMPTS:
                if not _looks_transient(e):
                    raise
                raise ActionFailedException(
                    f"the {app} unit was restarting while the overrides were "
                    f"applied (attaching a resource recreates the pod); the "
                    f"override may already be in effect, check with "
                    f"`sunbeam config-file override get {app}`: {e}"
                ) from e
            LOG.debug(
                "apply action on %s failed without a verdict (attempt %d/%d): %s",
                app,
                attempt,
                APPLY_ATTEMPTS,
                e,
            )
            wait_for_settle(jhelper, app, model)
            unit = jhelper.get_leader_unit(app, model)
            attempt += 1
            continue
        return parse_validation_result(results)


def get_applied_overrides(
    jhelper: JujuHelper,
    app: str,
    model: str = OPENSTACK_MODEL,
) -> list[str]:
    """Return the drop-in paths the charm reports as written on the unit."""
    check_charm_support(jhelper, app, model=model)
    unit = jhelper.get_leader_unit(app, model)
    results = jhelper.run_action(unit, model, GET_OVERRIDES_ACTION, {})
    return _parse_json_list(results.get("applied"))


def validate_enabled_overrides(
    deployment: "Deployment",
) -> list[tuple[str, list[str]]]:
    """Re-validate every enabled override against the deployed services.

    Enabling or disabling a feature, or resizing the deployment, changes the
    configuration and policy the services run with, which can invalidate an
    override that validated before.  Running the validation again keeps the
    stored overrides honest instead of leaving a charm in blocked state.

    :returns: one (application, errors) entry per override that no longer
              validates; empty when every enabled override is still valid.
    """
    client = deployment.get_client()
    overrides = read_overrides(client)
    jhelper = JujuHelper(deployment.juju_controller)
    failures: list[tuple[str, list[str]]] = []
    for app in sorted(overrides):
        record = overrides[app]
        if not record.get("enabled"):
            continue
        errors: list[str] = []
        for content in (record.get("config"), record.get("policy")):
            if not content:
                continue
            try:
                result = validate_overrides(jhelper, app, content)
            except (ConfigOverridesError, JujuException) as e:
                errors.append(str(e))
                continue
            if not result.valid:
                errors.extend(result.errors or ["override no longer valid"])
        if errors:
            failures.append((app, errors))
    return failures


def require_valid_overrides(deployment: "Deployment", console: "Console") -> None:
    """Fail when an enabled override no longer validates.

    Used before a change that can invalidate overrides (enabling a feature,
    resizing the deployment) so the change is reported as failed instead of
    landing with an override that leaves a charm blocked.

    :raises ConfigOverridesError: naming the applications that failed.
    """
    failures = validate_enabled_overrides(deployment)
    for app, errors in failures:
        console.print(f"[red]error:[/red] {app}: {'; '.join(errors)}")
    if failures:
        raise ConfigOverridesError(
            "config-file override validation failed for "
            + ", ".join(app for app, _ in failures)
        )


def report_stale_overrides(deployment: "Deployment", console: "Console") -> None:
    """Warn about enabled overrides that no longer validate.

    Used after a change that already moved the deployment: the override
    cannot hold the change back anymore, but it is no longer in effect.
    """
    for app, errors in validate_enabled_overrides(deployment):
        console.print(
            f"[yellow]warning:[/yellow] config-file override for {app} no "
            f"longer validates: {'; '.join(errors)}\n"
            f"Inspect it with `sunbeam config-file override get {app}` and "
            f"fix or disable it."
        )
