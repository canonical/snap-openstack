# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Group resolution and scoped terraform apply for control-plane upgrades.

The control-plane handler reads upgrade groups from the orchestration
metadata, upgrades each group via scoped terraform apply, waits for
convergence, and persists per-group state.

Each group is upgraded independently: if one group fails, the handler
returns failure and the operator can retry that group. Completed
groups are skipped on resume.
"""

from __future__ import annotations

import datetime
import logging
import typing

import click
import yaml
from rich.console import Console

from sunbeam.core.common import RiskLevel, infer_risk
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuWaitException
from sunbeam.core.manifest import Manifest, embedded_manifest_path
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.steps.openstack import CONFIG_KEY as OPENSTACK_CONFIG_KEY
from sunbeam.upgrades.control_plane.actions import (
    run_post_actions,
    run_pre_actions,
)
from sunbeam.upgrades.coordinator import (
    PhaseResult,
    ReleaseUpgradeCoordinator,
)
from sunbeam.upgrades.errors import UpgradeErrorCode
from sunbeam.upgrades.metadata import HopMetadata
from sunbeam.upgrades.state import Group, LastError, PhaseStatus, UpgradeState

LOG = logging.getLogger(__name__)
console = Console()


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _load_target_charm_manifests(to_release: str) -> dict[str, dict]:
    """Load per-charm manifest entries from the snap's embedded target manifest.

    Reads ``etc/manifests/<to_release>/<risk>.yml`` from the snap and
    returns a mapping of charm name to its raw manifest dict (channel,
    revision, config, resources — whatever the embedded manifest carries).

    :param to_release: target release, e.g. "2026.1"
    :returns: charm name -> raw manifest dict, or empty dict if not found
    """
    from snaphelpers import Snap

    try:
        snap = Snap()
        risk = infer_risk(snap)
        if risk == RiskLevel.STABLE:
            risk_str = "stable"
        else:
            risk_str = str(risk)
        path = embedded_manifest_path(snap, to_release, risk_str)
        data = yaml.safe_load(path.read_text())
        charms = data.get("core", {}).get("software", {}).get("charms", {})
        return dict(charms.items())
    except Exception as e:
        LOG.warning("Could not load target manifest for %s: %s", to_release, e)
        return {}


def _terraform_targets_for_charms(
    charms: list[str], terraform_targets: dict[str, list[str]]
) -> list[str]:
    """Build terraform ``-target`` CLI args from the group's metadata.

    Reads the ``terraform_targets`` mapping from the upgrade metadata
    (``upgrade.yml``). Each charm maps to one or more terraform resource
    addresses. Integrations are NOT targeted — they are applied in the
    reapply-terraform finalize step (full apply without -target) after
    both ends are upgraded.

    :param charms: charm names being upgraded
    :param terraform_targets: per-app target addresses from group metadata
    :returns: list of ``-target=<address>`` strings for terraform CLI
    :raises KeyError: if any charm is not in the mapping
    """
    targets: list[str] = []
    for charm in charms:
        for addr in terraform_targets[charm]:
            targets.append(f"-target={addr}")
    return targets


class ControlPlaneHandler:
    """Phase handler for the control-plane upgrade.

    Reads groups from metadata, upgrades each via scoped terraform
    apply, waits for convergence, and persists per-group state.

    Implements the PhaseHandler protocol.
    """

    def __init__(self, deployment: Deployment, to_release: str = ""):
        self.deployment = deployment
        self.to_release = to_release
        self._tfhelper: TerraformHelper | None = None
        self._manifest: Manifest | None = None
        self._jhelper: JujuHelper | None = None
        self._target_channels: dict[str, dict] | None = None

    @property
    def tfhelper(self) -> TerraformHelper:
        """Return the TerraformHelper for the openstack plan."""
        if self._tfhelper is None:
            self._tfhelper = self.deployment.get_tfhelper("openstack-plan")
        return self._tfhelper

    @property
    def manifest(self) -> Manifest:
        """Return the deployment manifest."""
        if self._manifest is None:
            self._manifest = self.deployment.get_manifest()
        return self._manifest

    @property
    def jhelper(self) -> JujuHelper:
        """Return a JujuHelper for this deployment."""
        if self._jhelper is None:
            self._jhelper = self.deployment.get_juju_helper()
        return self._jhelper

    @property
    def target_charms(self) -> dict[str, dict]:
        """Per-charm manifest entries from the snap's embedded target manifest."""
        if self._target_channels is None:
            self._target_channels = _load_target_charm_manifests(self.to_release)
        return self._target_channels

    def _override_charm_manifests(self, charms: list[str]) -> None:
        """Override manifest entries for the group's charms from the target release.

        Replaces the full CharmManifest (channel, revision, config, resources)
        for the group's charms with values from the snap's embedded target
        manifest. Other charms keep their current (clusterd) values.
        """
        if not self.to_release:
            return
        from sunbeam.core.manifest import CharmManifest

        for charm in charms:
            target_cfg = self.target_charms.get(charm)
            if target_cfg and charm in self.manifest.core.software.charms:
                old = self.manifest.core.software.charms[charm].channel
                self.manifest.core.software.charms[charm] = CharmManifest(**target_cfg)
                LOG.info(
                    "overrode %s channel: %s -> %s",
                    charm,
                    old,
                    target_cfg.get("channel"),
                )
            elif target_cfg:
                LOG.warning(
                    "charm %s not in deployment manifest, skipping override",
                    charm,
                )

    def run(
        self,
        coordinator: ReleaseUpgradeCoordinator,
        metadata: HopMetadata | None,
        state: UpgradeState,
    ) -> PhaseResult:
        """Execute the control-plane upgrade.

        Upgrades each metadata group in order. Skips completed groups
        (resume). Returns failure on the first failed group.
        """
        if metadata is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_MISSING,
                error_message="No metadata loaded for control-plane phase",
            )

        hop = coordinator.get_current_hop()
        if hop is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.HOP_INVALID_TRANSITION,
                error_message="No active hop",
            )

        control_plane = hop.phases.control_plane

        # Set phase status on first group start
        if control_plane.status == PhaseStatus.PENDING:
            control_plane.status = PhaseStatus.IN_PROGRESS
            hop.phase = "control_plane"
            coordinator.persist_state()

        for group_meta in metadata.control_plane_groups:
            group_name = group_meta.name

            group_state = control_plane.groups.get(group_name)
            if group_state is None:
                group_state = Group()
                control_plane.groups[group_name] = group_state

            if group_state.status == PhaseStatus.COMPLETED:
                LOG.info("skipping completed group %s", group_name)
                continue

            group_state.status = PhaseStatus.IN_PROGRESS
            group_state.started_at = _now_iso()
            coordinator.persist_state()

            result = self._upgrade_group(group_meta, group_name)

            if result.success:
                group_state.status = PhaseStatus.COMPLETED
                group_state.completed_at = _now_iso()
                coordinator.persist_state()
                LOG.info("group %s completed", group_name)
            else:
                group_state.status = PhaseStatus.FAILED
                group_state.last_error = LastError(
                    code=result.error_code.value if result.error_code else "",
                    message=result.error_message or "",
                )
                coordinator.persist_state()
                LOG.warning("group %s failed: %s", group_name, result.error_message)
                return result

        # All groups done — mark phase completed
        control_plane.status = PhaseStatus.COMPLETED
        coordinator.persist_state()

        return PhaseResult(success=True)

    def plan_group(
        self,
        group_meta: typing.Any,
    ) -> list[dict]:
        """Run terraform plan for a single group and return change events.

        Updates tfvars for the group's charms and runs ``terraform plan``
        without applying. Does not modify clusterd state.

        Runs both JSON and text plan: JSON events are returned for
        user-facing display; text output is logged at debug level for
        human-readable ``+``/``-`` diff in the sunbeam log file.

        :param group_meta: ControlPlaneGroup metadata for the group
        :returns: list of terraform plan JSON events
        :raises TerraformException: if the plan command fails
        """
        client = self.deployment.get_client()
        charms = group_meta.apps
        target_args = _terraform_targets_for_charms(
            charms, group_meta.terraform_targets
        )

        self._override_charm_manifests(charms)
        self.tfhelper.init()
        events = self.tfhelper.update_partial_tfvars_and_plan_tf(
            client,
            self.manifest,
            charms,
            OPENSTACK_CONFIG_KEY,
            tf_plan_extra_args=target_args,
        )

        # Run text plan for debug logging (tfvars already written above)
        try:
            plan_text = self.tfhelper.terraform_plan_text(extra_args=target_args)
            LOG.debug(
                "Terraform plan (text) for group %s:\n%s",
                group_meta.name,
                plan_text,
            )
        except TerraformException as e:
            LOG.warning(
                "Terraform text plan failed for group %s: %s",
                group_meta.name,
                e,
            )

        return events

    def run_group(
        self,
        coordinator: ReleaseUpgradeCoordinator,
        metadata: HopMetadata | None,
        group_name: str,
    ) -> PhaseResult:
        """Upgrade a single group by name.

        Does not skip completed groups — use run() for resume behavior.
        """
        if metadata is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_MISSING,
                error_message="No metadata loaded",
            )

        hop = coordinator.get_current_hop()
        if hop is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.HOP_INVALID_TRANSITION,
                error_message="No active hop",
            )

        group_meta = None
        for g in metadata.control_plane_groups:
            if g.name == group_name:
                group_meta = g
                break
        if group_meta is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_INVALID,
                error_message=f"Group {group_name} not found in metadata",
            )

        control_plane = hop.phases.control_plane
        group_state = control_plane.groups.get(group_name)
        if group_state is None:
            group_state = Group()
            control_plane.groups[group_name] = group_state

        # Set phase status on first group start
        if control_plane.status == PhaseStatus.PENDING:
            control_plane.status = PhaseStatus.IN_PROGRESS
            hop.phase = "control_plane"
            coordinator.persist_state()

        group_state.status = PhaseStatus.IN_PROGRESS
        group_state.started_at = _now_iso()
        coordinator.persist_state()

        result = self._upgrade_group(group_meta, group_name)
        if result.success:
            group_state.status = PhaseStatus.COMPLETED
            group_state.completed_at = _now_iso()

            # If this was the last group in the metadata and all groups
            # completed, mark phase completed
            all_completed = all(
                gs.status == PhaseStatus.COMPLETED
                for gs in control_plane.groups.values()
            )
            is_last = group_name == metadata.control_plane_groups[-1].name
            if is_last and all_completed:
                control_plane.status = PhaseStatus.COMPLETED
                coordinator.persist_state()
        else:
            group_state.status = PhaseStatus.FAILED
            group_state.last_error = LastError(
                code=result.error_code.value if result.error_code else "",
                message=result.error_message or "",
            )
        coordinator.persist_state()
        return result

    def run_application(
        self,
        coordinator: ReleaseUpgradeCoordinator,
        metadata: HopMetadata | None,
        charm_name: str,
    ) -> PhaseResult:
        """Upgrade a single application via scoped terraform apply.

        Does not run pre/post actions (those are group-level).

        :param charm_name: charm name (e.g. 'placement-k8s'), resolved
            from the juju app name by the CLI.
        """
        if metadata is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_MISSING,
                error_message="No metadata loaded",
            )

        hop = coordinator.get_current_hop()
        if hop is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.HOP_INVALID_TRANSITION,
                error_message="No active hop",
            )

        # Verify the charm exists in some group
        group_meta = None
        for g in metadata.control_plane_groups:
            if charm_name in g.apps:
                group_meta = g
                break
        if group_meta is None:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_INVALID,
                error_message=f"Charm {charm_name} not found in any group",
            )

        # Mark group as in_progress (single-app upgrade doesn't complete the group)
        control_plane = hop.phases.control_plane
        group_state = control_plane.groups.get(group_meta.name)
        if group_state is None:
            group_state = Group()
            control_plane.groups[group_meta.name] = group_state
        if group_state.status != PhaseStatus.COMPLETED:
            group_state.status = PhaseStatus.IN_PROGRESS
            group_state.started_at = _now_iso()
            coordinator.persist_state()

        # Set phase status on first app start
        if control_plane.status == PhaseStatus.PENDING:
            control_plane.status = PhaseStatus.IN_PROGRESS
            hop.phase = "control_plane"
            coordinator.persist_state()

        client = self.deployment.get_client()
        target_args = _terraform_targets_for_charms(
            [charm_name], group_meta.terraform_targets
        )
        self._override_charm_manifests([charm_name])

        # Resolve charm name to deployed juju app name for Juju operations
        status = self.jhelper.get_model_status(OPENSTACK_MODEL)
        juju_app_names = [
            name for name, app in status.apps.items() if app.charm == charm_name
        ]
        if not juju_app_names:
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.METADATA_INVALID,
                error_message=f"No deployed application found for charm {charm_name}",
            )

        try:
            with console.status(f"  {charm_name}: applying terraform plan..."):
                self.tfhelper.init()
                self.tfhelper.update_partial_tfvars_and_apply_tf(
                    client,
                    self.manifest,
                    [charm_name],
                    OPENSTACK_CONFIG_KEY,
                    tf_apply_extra_args=target_args,
                )
        except TerraformException as e:
            click.echo(f"  {charm_name}: terraform apply FAILED")
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.CONTROL_PLANE_APPLY_FAILED,
                error_message=f"Terraform init/apply failed for {charm_name}: {e}",
            )

        try:
            with console.status(f"  {charm_name}: waiting for convergence..."):
                self.jhelper.wait_until_desired_status(
                    OPENSTACK_MODEL,
                    juju_app_names,
                    status=["active"],
                    timeout=600,
                )
        except (JujuWaitException, TimeoutError) as e:
            click.echo(f"  {charm_name}: convergence timeout")
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.CONTROL_PLANE_CONVERGENCE_TIMEOUT,
                error_message=f"{charm_name} did not converge: {e}",
            )

        # If this was the only app in the group, mark group as completed
        if len(group_meta.apps) == 1:
            group_state.status = PhaseStatus.COMPLETED
            group_state.completed_at = _now_iso()
            coordinator.persist_state()

            # If this was the last group in the metadata and all groups
            # completed, mark phase completed
            all_completed = all(
                gs.status == PhaseStatus.COMPLETED
                for gs in control_plane.groups.values()
            )
            is_last = group_meta.name == metadata.control_plane_groups[-1].name
            if is_last and all_completed:
                control_plane.status = PhaseStatus.COMPLETED
                coordinator.persist_state()

        click.echo(f"  {charm_name}: completed")

        return PhaseResult(success=True)

    def _upgrade_group(
        self,
        group_meta: typing.Any,
        group_name: str,
    ) -> PhaseResult:
        """Upgrade a single group: pre-actions, apply, converge, post-actions."""
        charms = group_meta.apps
        LOG.info(
            "upgrading group %s: apps=%s timeout=%ds",
            group_name,
            charms,
            group_meta.ready_timeout_sec,
        )

        # Resolve charm names to deployed app names, skipping undeployed charms
        status = self.jhelper.get_model_status(OPENSTACK_MODEL)
        deployed_charms = {app.charm for app in status.apps.values()}
        charms = [c for c in charms if c in deployed_charms]
        app_names = [name for name, app in status.apps.items() if app.charm in charms]
        if not charms:
            LOG.info("group %s has no deployed charms — skipping", group_name)
            click.echo(f"  {group_name}: no deployed charms — skipping")
            return PhaseResult(success=True)
        LOG.info("resolved charms %s to apps %s", charms, app_names)

        # Pre-upgrade actions (use app names, not charm names)
        with console.status(f"  {group_name}: running pre-upgrade actions..."):
            pre_result = run_pre_actions(self.jhelper, group_meta.pre_actions)
        if not pre_result.success:
            click.echo(f"  {group_name}: pre-upgrade actions FAILED")
            # Still run post-actions as cleanup attempt
            run_post_actions(self.jhelper, group_meta.post_actions)
            return pre_result

        client = self.deployment.get_client()
        target_args = _terraform_targets_for_charms(
            charms, group_meta.terraform_targets
        )

        self._override_charm_manifests(charms)
        try:
            # ponytail: init re-resolves providers from the snap mirror; a snap
            # refresh can bump the juju provider version, leaving the .terraform
            # dir stale and apply failing with "unavailable provider".
            with console.status(f"  {group_name}: applying terraform plan..."):
                self.tfhelper.init()
                self.tfhelper.update_partial_tfvars_and_apply_tf(
                    client,
                    self.manifest,
                    charms,
                    OPENSTACK_CONFIG_KEY,
                    tf_apply_extra_args=target_args,
                )
        except TerraformException as e:
            click.echo(f"  {group_name}: terraform apply FAILED")
            # Run post-actions as cleanup even on failure
            run_post_actions(self.jhelper, group_meta.post_actions)
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.CONTROL_PLANE_APPLY_FAILED,
                error_message=f"Terraform init/apply failed for {group_name}: {e}",
            )

        try:
            with console.status(
                f"  {group_name}: waiting for convergence "
                f"({group_meta.ready_timeout_sec}s)..."
            ):
                self.jhelper.wait_until_desired_status(
                    OPENSTACK_MODEL,
                    app_names,
                    status=["active"],
                    timeout=group_meta.ready_timeout_sec,
                )
        except (JujuWaitException, TimeoutError) as e:
            click.echo(f"  {group_name}: convergence timeout")
            # Run post-actions as cleanup even on failure
            run_post_actions(self.jhelper, group_meta.post_actions)
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.CONTROL_PLANE_CONVERGENCE_TIMEOUT,
                error_message=(
                    f"Group {group_name} did not converge within "
                    f"{group_meta.ready_timeout_sec}s: {e}"
                ),
            )

        # Post-upgrade actions
        with console.status(f"  {group_name}: running post-upgrade actions..."):
            post_result = run_post_actions(self.jhelper, group_meta.post_actions)
        if not post_result.success:
            click.echo(f"  {group_name}: post-upgrade actions FAILED")
            return PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                error_message=(
                    f"Post-upgrade action failed for group {group_name}: "
                    f"{post_result.error_message}. The upgrade applied "
                    "successfully but post-upgrade actions may not have "
                    "completed — manual intervention may be required."
                ),
            )

        click.echo(f"  {group_name}: completed")
        return PhaseResult(success=True)
