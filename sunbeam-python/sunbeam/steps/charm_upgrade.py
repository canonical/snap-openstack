# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Shared logic for deciding whether a charm needs refreshing."""

import logging
from typing import NamedTuple

import jubilant.statustypes

from sunbeam.core.common import Result, ResultType
from sunbeam.core.juju import ApplicationNotFoundException, JujuException, JujuHelper
from sunbeam.core.manifest import Manifest

LOG = logging.getLogger(__name__)


class CharmRefreshDecision(NamedTuple):
    """Decision returned by check_charm_needs_refresh.

    Attributes:
        result: SKIPPED (already up-to-date or not deployed), FAILED (invalid
            channel/track), or COMPLETED (refresh needed).
        effective_channel: The resolved channel to pass to charm_refresh.
        needs_channel_flag: True when --channel must be passed to charm_refresh
            (e.g. a risk-level change or an allowed track upgrade).
        effective_revision: Pinned revision to pass to charm_refresh, or None
            for latest in channel.
        app_not_deployed: True when SKIPPED because the application is not
            deployed (as opposed to already up-to-date).
    """

    result: Result
    effective_channel: str
    needs_channel_flag: bool = False
    effective_revision: int | None = None
    app_not_deployed: bool = False


def _track_version(track: str) -> tuple:
    """Parse a version track like '1.18' into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in track.split("."))
    except ValueError:
        return (0,)


def _check_revision_only(
    jhelper: JujuHelper,
    charm_name: str,
    app: "jubilant.statustypes.AppStatus",
    manifest_revision: int,
    deployed_channel: str | None,
    effective_channel: str,
    support_track_upgrades: bool,
) -> CharmRefreshDecision:
    """Handle the revision-only path (manifest has revision but no channel)."""
    if app.charm_rev == manifest_revision:
        return CharmRefreshDecision(
            result=Result(
                ResultType.SKIPPED,
                f"{charm_name} already at manifest pinned revision {manifest_revision}",
            ),
            effective_channel=effective_channel,
        )
    if not support_track_upgrades and deployed_channel:
        deployed_track = deployed_channel.split("/")[0]
        try:
            revision_channel = jhelper.get_charm_channel_for_revision(
                charm_name, manifest_revision
            )
        except JujuException as e:
            LOG.debug(
                "Could not determine channel for revision %s: %s",
                manifest_revision,
                e,
            )
            revision_channel = None

        if revision_channel:
            revision_track = revision_channel.split("/")[0]
            if deployed_track != revision_track:
                return CharmRefreshDecision(
                    result=Result(
                        ResultType.FAILED,
                        f"Revision {manifest_revision} belongs to channel "
                        f"{revision_channel!r} (track {revision_track!r}), "
                        f"but the deployed charm is on track "
                        f"{deployed_track!r}. "
                        "Track changes are not supported by this command.",
                    ),
                    effective_channel=effective_channel,
                )
    return CharmRefreshDecision(
        result=Result(ResultType.COMPLETED),
        effective_channel=effective_channel,
        effective_revision=manifest_revision,
    )


def _check_patch_upgrade(
    jhelper: JujuHelper,
    charm_name: str,
    app: "jubilant.statustypes.AppStatus",
    effective_channel: str,
) -> CharmRefreshDecision:
    """Handle patch upgrade: check whether a newer revision is available."""
    try:
        if app.base:
            base = f"{app.base.name}@{app.base.channel}"
            latest_revs = jhelper.get_available_charm_revisions(
                charm_name, effective_channel, base
            )
        else:
            latest_revs = jhelper.get_available_charm_revisions(
                charm_name, effective_channel
            )
    except JujuException as e:
        LOG.debug("Could not determine latest revision for %s: %s", charm_name, e)
        return CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=effective_channel,
        )

    if app.charm_rev in latest_revs.values():
        return CharmRefreshDecision(
            result=Result(
                ResultType.SKIPPED,
                f"{charm_name} is already at the latest revision"
                f" {app.charm_rev} for channel {effective_channel}",
            ),
            effective_channel=effective_channel,
        )
    return CharmRefreshDecision(
        result=Result(ResultType.COMPLETED),
        effective_channel=effective_channel,
    )


def check_charm_needs_refresh(
    jhelper: JujuHelper,
    manifest: Manifest,
    charm_name: str,
    model: str,
    application: str,
    default_channel: str,
    support_track_upgrades: bool = False,
) -> CharmRefreshDecision:
    """Determine whether a charm needs refreshing and how.

    Effective channel resolution (no manifest channel set):
    - minimum_track is derived from default_channel.
    - If the deployed track meets the minimum, the deployed channel is used.
    - If the deployed track is below the minimum and support_track_upgrades is
      True, fall back to default_channel (e.g. vault).
    - If the deployed track is below the minimum and support_track_upgrades is
      False, emit a warning and stay on the deployed channel for a same-track
      patch refresh (e.g. k8s).
    - A manifest channel whose track is below the minimum always returns FAILED.

    Args:
        jhelper: JujuHelper instance.
        manifest: Manifest to read charm channel/revision from.
        charm_name: CharmHub charm name (e.g. "vault-k8s", "k8s").
        model: Juju model name.
        application: Juju application name.
        default_channel: Fallback channel when the deployed track is below
            the derived minimum or there is no deployed channel.
        support_track_upgrades: If True, track changes are allowed (vault).
            If False, track changes return FAILED (k8s).

    Returns:
        CharmRefreshDecision with result, effective_channel, needs_channel_flag.
    """
    try:
        app = jhelper.get_application(application, model)
    except ApplicationNotFoundException:
        return CharmRefreshDecision(
            result=Result(
                ResultType.SKIPPED,
                f"{application!r} application has not been deployed yet",
            ),
            effective_channel=default_channel,
            app_not_deployed=True,
        )

    charm_manifest = manifest.find_charm(charm_name)
    manifest_channel: str | None = charm_manifest.channel if charm_manifest else None
    manifest_revision: int | None = charm_manifest.revision if charm_manifest else None
    deployed_channel: str | None = app.charm_channel

    # Minimum track is always derived from default_channel.
    minimum_track: str = default_channel.split("/")[0]

    # ------------------------------------------------------------------
    # Effective channel resolution
    # ------------------------------------------------------------------
    # Manifest channel wins when explicitly set.
    # Without a manifest channel: stay on the deployed channel if its track
    # meets the minimum, otherwise fall back to default_channel (vault) or
    # fail (k8s, where track changes are not allowed).
    if manifest_channel:
        effective_channel = manifest_channel
    elif deployed_channel and (
        _track_version(deployed_channel.split("/")[0]) >= _track_version(minimum_track)
    ):
        effective_channel = deployed_channel
    elif deployed_channel and not support_track_upgrades:
        # Deployed track is below minimum but track changes are not supported.
        # Warn and stay on the deployed channel for a same-track patch refresh.
        LOG.warning(
            "Deployed track %r is below the minimum supported track %r. "
            "Refreshing within the deployed track.",
            deployed_channel.split("/")[0],
            minimum_track,
        )
        effective_channel = deployed_channel
    else:
        effective_channel = default_channel

    effective_track: str = effective_channel.split("/")[0]

    # ------------------------------------------------------------------
    # Downgrade guard: manifest channel below minimum_track → FAILED
    # ------------------------------------------------------------------
    if manifest_channel:
        manifest_track = manifest_channel.split("/")[0]
        if _track_version(manifest_track) < _track_version(minimum_track):
            LOG.warning(
                "Manifest channel %r track is below minimum supported track %r.",
                manifest_channel,
                minimum_track,
            )
            return CharmRefreshDecision(
                result=Result(
                    ResultType.FAILED,
                    f"Manifest channel {manifest_channel!r} track is below "
                    f"minimum supported track {minimum_track!r}. "
                    "Cannot refresh to this channel.",
                ),
                effective_channel=effective_channel,
            )

    # ------------------------------------------------------------------
    # Revision-only path: manifest has revision but no channel
    # ------------------------------------------------------------------
    if manifest_revision is not None and not manifest_channel:
        return _check_revision_only(
            jhelper,
            charm_name,
            app,
            manifest_revision,
            deployed_channel,
            effective_channel,
            support_track_upgrades,
        )

    # ------------------------------------------------------------------
    # Track change guard (when support_track_upgrades=False)
    # ------------------------------------------------------------------
    if not support_track_upgrades and manifest_channel and deployed_channel:
        deployed_track = deployed_channel.split("/")[0]
        if deployed_track != effective_track:
            return CharmRefreshDecision(
                result=Result(
                    ResultType.FAILED,
                    f"Channel track change from {deployed_track!r} to "
                    f"{effective_track!r} is not supported by this command.",
                ),
                effective_channel=effective_channel,
            )

    # needs_channel_flag: True when effective channel differs from the
    # deployed one (risk-level change within same track, or track upgrade
    # for charms that allow it).
    needs_channel_flag = bool(
        deployed_channel and deployed_channel != effective_channel
    )

    # ------------------------------------------------------------------
    # Exact revision + channel match → SKIPPED
    # ------------------------------------------------------------------
    if manifest_revision is not None:
        if deployed_channel == effective_channel and app.charm_rev == manifest_revision:
            return CharmRefreshDecision(
                result=Result(
                    ResultType.SKIPPED,
                    f"{charm_name} already at manifest pinned revision"
                    f" {manifest_revision} for channel {effective_channel}",
                ),
                effective_channel=effective_channel,
                needs_channel_flag=needs_channel_flag,
            )
        return CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=effective_channel,
            needs_channel_flag=needs_channel_flag,
            effective_revision=manifest_revision,
        )

    # ------------------------------------------------------------------
    # Risk/track change: always proceed without a revision check
    # ------------------------------------------------------------------
    if needs_channel_flag:
        return CharmRefreshDecision(
            result=Result(ResultType.COMPLETED),
            effective_channel=effective_channel,
            needs_channel_flag=True,
        )

    # ------------------------------------------------------------------
    # Patch upgrade: check whether a newer revision is available
    # ------------------------------------------------------------------
    return _check_patch_upgrade(jhelper, charm_name, app, effective_channel)
