# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Upgrade metadata schema and loader.

Defines the orchestration metadata that drives the release-upgrade engine.
The metadata tells the engine WHAT to do (group ordering, which actions to
run, step sequences, timeouts) — not HOW to do it (action implementations
live in charms, engine steps in the coordinator).

This is distinct from the deployment manifest (``manifests/<release>/<risk>.yml``)
which carries charm channels and config. The upgrade metadata carries
orchestration: which groups exist, their order, which actions to run on
which apps, and per-phase step sequences.

Adding a new release = a new ``manifests/<release>/upgrade.yml`` file. The
engine reads it and executes generically — no Python code changes needed.
"""

from __future__ import annotations

import enum
import logging
from pathlib import Path

import pydantic
import yaml
from snaphelpers import Snap

LOG = logging.getLogger(__name__)

# Default manifest path relative to the snap root.
DEFAULT_UPGRADE_MANIFEST_DIR = Path(Snap().paths.snap / "etc" / "manifests")


class ActionScope(str, enum.Enum):
    """Which units to run a juju action on."""

    LEADER = "leader"
    ALL_UNITS = "all-units"


class StepType(str, enum.Enum):
    """Type of a finalize step."""

    # Run a juju action on specified apps
    ACTION = "action"
    # Call an engine built-in handler by name
    ENGINE = "engine"


class ActionSpec(pydantic.BaseModel):
    """A juju action to run on specific apps.

    The engine runs ``juju run <app>/<unit> <action>`` for each app x unit
    (or leader only). It does not need to know what the action does — that's
    the charm's responsibility.
    """

    action: str = pydantic.Field(description="Juju action name, e.g. pre-upgrade")
    apps: list[str] = pydantic.Field(description="App names to run the action on")
    scope: ActionScope = pydantic.Field(
        default=ActionScope.LEADER,
        description="Run on leader only, or on all units",
    )


class ControlPlaneGroup(pydantic.BaseModel):
    """A control-plane upgrade group.

    Groups are upgraded in the order they appear in the metadata. Each group's
    apps are upgraded together via scoped terraform apply, with pre-upgrade
    actions before and post-upgrade actions after.
    """

    name: str = pydantic.Field(description="Group name, e.g. identity-core")
    apps: list[str] = pydantic.Field(description="App names in this group")
    ready_timeout_sec: int = pydantic.Field(
        default=600,
        description="Seconds to wait for all units to reach active/idle",
    )
    pre_actions: list[ActionSpec] = pydantic.Field(
        default_factory=list,
        description="Actions to run before terraform apply",
    )
    post_actions: list[ActionSpec] = pydantic.Field(
        default_factory=list,
        description="Actions to run after units reach active/idle",
    )
    terraform_targets: dict[str, list[str]] = pydantic.Field(
        default_factory=dict,
        description=(
            "Per-app terraform -target addresses for scoped apply. "
            "Keys are charm names (e.g. keystone-k8s), values are "
            "terraform resource addresses (e.g. [module.keystone]). "
            "Integrations are NOT listed — they are applied in the "
            "reapply-terraform finalize step after both ends are upgraded."
        ),
    )


class ComputeConfig(pydantic.BaseModel):
    """Compute node principal + auxiliary apps for the data plane."""

    principal: str = pydantic.Field(
        description="Principal app, e.g. openstack-hypervisor"
    )
    auxiliary: list[str] = pydantic.Field(
        default_factory=list,
        description="Co-located principal charms to refresh alongside"
        " (NOT Juju subordinates — those auto-track their principal)",
    )


class DataplaneConfig(pydantic.BaseModel):
    """Data-plane configuration.

    The step sequence is defined in the metadata so a future release can
    add/remove steps. Each step name maps to an engine handler via a
    dispatch table. Some steps call juju actions (disable, enable,
    refresh-snap); others are engine operations (resolve, verify, mark).
    """

    compute: ComputeConfig = pydantic.Field(
        description="Compute node principal + auxiliary apps"
    )
    registration_timeout_sec: int = pydantic.Field(
        default=300,
        description="Seconds to wait for the principal to re-register after refresh",
    )
    steps: list[str] = pydantic.Field(
        default_factory=lambda: [
            "resolve",
            "disable-scheduling",
            "pre-upgrade-checks",
            "refresh-principal",
            "refresh-auxiliary",
            "verify-registration",
            "verify-auxiliary",
            "enable-scheduling",
            "mark-complete",
        ],
        description="Ordered step names; each maps to an engine handler",
    )


class StorageConfig(pydantic.BaseModel):
    """Storage-plane configuration (Ceph-backed cinder-volume only)."""

    principal: str = pydantic.Field(description="Principal app, e.g. cinder-volume")
    registration_timeout_sec: int = pydantic.Field(
        default=300,
        description="Seconds to wait for the principal to re-register",
    )
    steps: list[str] = pydantic.Field(
        default_factory=lambda: [
            "resolve",
            "pre-upgrade-checks",
            "refresh-snap",
            "verify-registration",
            "mark-complete",
        ],
        description="Ordered step names; each maps to an engine handler",
    )


class FinalizeStep(pydantic.BaseModel):
    """A single finalize step.

    type=action: run a juju action on specified apps (with scope).
    type=engine: call a built-in engine handler by name.

    Adding a new action step is metadata-only (if the charm exposes the
    action). Adding a new engine step requires code — but that's correct:
    a genuinely new type of operation is new engine capability.
    """

    name: str = pydantic.Field(description="Step name for logging and state")
    type: StepType = pydantic.Field(description="action or engine")
    action: str | None = pydantic.Field(
        default=None,
        description="Juju action name (required if type=action)",
    )
    apps: list[str] | None = pydantic.Field(
        default=None,
        description="Apps to run the action on (required if type=action)",
    )
    scope: ActionScope = pydantic.Field(
        default=ActionScope.LEADER,
        description="Run on leader only, or on all units",
    )

    @pydantic.model_validator(mode="after")
    def _validate_action_fields(self) -> "FinalizeStep":
        if self.type == StepType.ACTION and not self.action:
            raise ValueError("action is required when type=action")
        if self.type == StepType.ACTION and not self.apps:
            raise ValueError("apps is required when type=action")
        return self


class Compatibility(pydantic.BaseModel):
    """Compatibility actions for a hop (pre-hop and post-hop).

    For 2024.1->2025.1, both are empty: nova's upgrade_levels=auto is a
    permanent default, and cinder's RPC cap is intrinsic. Future hops
    may need transient cap actions here.
    """

    pre_hop: list[ActionSpec] = pydantic.Field(default_factory=list)
    post_hop: list[ActionSpec] = pydantic.Field(default_factory=list)


class Prerequisite(pydantic.BaseModel):
    """An infrastructure prerequisite that must be completed before upgrade.

    These are operator-driven via existing ``sunbeam cluster refresh`` commands.
    Preflight verifies they have been completed.
    """

    type: str = pydantic.Field(description="snap_refresh or infra_refresh")
    channel: str | None = pydantic.Field(
        default=None, description="Target channel (for snap_refresh)"
    )
    component: str | None = pydantic.Field(
        default=None, description="Component name (for infra_refresh)"
    )


class HopMetadata(pydantic.BaseModel):
    """Orchestration metadata for a single upgrade hop.

    This is the top-level schema for ``manifests/<release>/upgrade.yml``.
    The engine reads it to know: which groups to upgrade in what order,
    which actions to run and when, what steps each phase executes, and
    what prerequisites must be met.
    """

    from_release: str = pydantic.Field(alias="from", description="Source release")
    to_release: str = pydantic.Field(alias="to", description="Target release")
    control_plane_groups: list[ControlPlaneGroup] = pydantic.Field(
        description="Ordered control-plane groups"
    )
    compatibility: Compatibility = pydantic.Field(default_factory=Compatibility)
    dataplane: DataplaneConfig = pydantic.Field(
        default_factory=lambda: DataplaneConfig(
            compute=ComputeConfig(
                principal="openstack-hypervisor",
                auxiliary=["epa-orchestrator", "openstack-network-agents"],
            )
        )
    )
    storage: StorageConfig = pydantic.Field(
        default_factory=lambda: StorageConfig(principal="cinder-volume")
    )
    finalize: list[FinalizeStep] = pydantic.Field(
        default_factory=list,
        description="Ordered finalize steps",
    )
    required_prerequisites: list[Prerequisite] = pydantic.Field(
        default_factory=list,
        description="Infrastructure refreshes required before upgrade",
    )


def load_upgrade_metadata(
    release: str, manifest_dir: Path | None = None
) -> HopMetadata:
    """Load upgrade metadata for a target release.

    Reads ``manifests/<release>/upgrade.yml`` from the snap-local manifests
    directory. The path is conventioned — no metadata_path field needed.

    :param release: Target release, e.g. "2025.1"
    :param manifest_dir: Override the manifests directory (for testing)
    :returns: Parsed and validated HopMetadata
    :raises FileNotFoundError: if the upgrade metadata file does not exist
    :raises ValueError: if the metadata fails validation
    """
    if manifest_dir is None:
        manifest_dir = DEFAULT_UPGRADE_MANIFEST_DIR

    path = manifest_dir / release / "upgrade.yml"
    LOG.debug("Loading upgrade metadata from %s", path)

    if not path.exists():
        raise FileNotFoundError(
            f"Upgrade metadata not found for release {release} at {path}"
        )

    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raise ValueError(f"Upgrade metadata file is empty: {path}")

    return HopMetadata.model_validate(raw)
