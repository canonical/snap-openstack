# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import Counter
from pathlib import Path

import click
import yaml
from rich.console import Console
from snaphelpers import Snap

from sunbeam.clusterd.service import ManifestItemNotFoundException
from sunbeam.core.common import RiskLevel, infer_risk, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import AddManifestStep
from sunbeam.steps.upgrades.base import UpgradeCoordinator
from sunbeam.steps.upgrades.inter_channel import ChannelUpgradeCoordinator
from sunbeam.steps.upgrades.intra_channel import LatestInChannelCoordinator
from sunbeam.utils import click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()
_KNOWN_RISKS = {r.value for r in RiskLevel}


def _stored_manifest_risk(client) -> str | None:
    """Infer the risk level that was in use when the stored manifest was written.

    Scans all charm channels in the stored manifest and returns the most
    common risk component (e.g. ``"stable"``, ``"beta"``).  Returns ``None``
    if the stored manifest cannot be read or contains no recognisable channels.
    """
    try:
        stored = client.cluster.get_latest_manifest()
    except ManifestItemNotFoundException:
        return None

    stored_content = yaml.safe_load(stored.get("data", "") or "") or {}
    charms = stored_content.get("core", {}).get("software", {}).get("charms", {}) or {}
    risk_counts: Counter = Counter()
    for charm_config in charms.values():
        if not isinstance(charm_config, dict):
            continue
        channel = charm_config.get("channel", "") or ""
        parts = channel.split("/")
        # channel format: track/risk or track/risk/branch
        if len(parts) >= 2 and parts[1] in _KNOWN_RISKS:
            risk_counts[parts[1]] += 1

    if not risk_counts:
        return None
    # Return the risk that appears most often across charm channels.
    return risk_counts.most_common(1)[0][0]


@click.command()
@click.option(
    "-c",
    "--clear-manifest",
    is_flag=True,
    default=False,
    help="Clear the manifest file.",
    type=bool,
)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--upgrade-release",
    is_flag=True,
    show_default=True,
    default=False,
    # note(gboutry): Hidden until supported
    hidden=True,
    help="Upgrade OpenStack release.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force refresh, skipping confirmation prompts.",
)
@click_option_show_hints
@click.pass_context
def refresh(
    ctx: click.Context,
    upgrade_release: bool,
    manifest_path: Path | None = None,
    clear_manifest: bool = False,
    force: bool = False,
    show_hints: bool = False,
) -> None:
    """Refresh deployment.

    Refresh the deployment. If --upgrade-release is supplied then charms are
    upgraded the channels aligned with this snap revision
    """
    if clear_manifest and manifest_path:
        raise click.ClickException(
            "Options manifest and clear_manifest are mutually exclusive"
        )

    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    if not manifest_path and not clear_manifest:
        # Warn only when the snap channel risk has changed since the manifest
        # was last stored (e.g. snap refreshed from stable to beta).  We
        # detect this by comparing the risk inferred from the current snap with
        # the dominant risk found in the charm channels of the stored manifest.
        # We intentionally do NOT compare full manifest content: users can add
        # extra charm overrides without changing their channel risk.
        snap_channel_changed = False
        try:
            current_risk = str(infer_risk(Snap()))
            stored_risk = _stored_manifest_risk(client)
            if stored_risk is not None and stored_risk != current_risk:
                snap_channel_changed = True
        except Exception:
            LOG.debug(
                "Could not compare manifest risk to detect snap channel change",
                exc_info=True,
            )

        if snap_channel_changed and not force:
            click.confirm(
                "The snap channel has changed since the last manifest update."
                " It is recommended to provide a manifest targeting the new"
                " channel's charm versions via"
                " `sunbeam cluster refresh -m`."
                "\nContinue anyway?",
                default=False,
                abort=True,
            )
    # Validate manifest file
    manifest = None
    if clear_manifest:
        run_plan([AddManifestStep(client, clear=True)], console, show_hints)
    elif manifest_path:
        manifest = deployment.get_manifest(manifest_path)
        run_plan([AddManifestStep(client, manifest_path)], console, show_hints)

    if not manifest:
        LOG.debug("Getting latest manifest from cluster db")
        manifest = deployment.get_manifest()

    LOG.debug(f"Manifest used for deployment - core: {manifest.core}")
    jhelper = JujuHelper(deployment.juju_controller)
    upgrade_coordinator: UpgradeCoordinator
    if upgrade_release:
        upgrade_coordinator = ChannelUpgradeCoordinator(
            deployment, client, jhelper, manifest
        )
        upgrade_coordinator.run_plan(show_hints)
    else:
        upgrade_coordinator = LatestInChannelCoordinator(
            deployment, client, jhelper, manifest
        )
        upgrade_coordinator.run_plan(show_hints)
    click.echo("Refresh complete.")
