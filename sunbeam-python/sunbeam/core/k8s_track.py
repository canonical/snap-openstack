# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Keep the Kubernetes deployment track independent of manifests and Juju drift."""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import tenacity
from jubilant.statustypes import AppStatus

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    TerraformPlanLockConflictException,
)
from sunbeam.core.charm_channel import (
    CharmChannelError,
    channel_track,
    normalize_explicit_channel,
    validate_revision_track,
)
from sunbeam.core.common import read_config, update_config
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    ModelNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.versions import K8S_CHANNEL, K8S_MIN_SUPPORTED_TRACK

K8S_TRACK_CONFIG_KEY = "K8SDeploymentTrack"
_TRACK_LOCK = "k8s-track"
_VERSION_TRACK = re.compile(r"[0-9]+\.[0-9]+")


def _k8s_channel(channel: str) -> str:
    """Require a Kubernetes minor version as the channel track."""
    normalized = normalize_explicit_channel(channel, "Kubernetes")
    if not _VERSION_TRACK.fullmatch(channel_track(normalized)):
        raise CharmChannelError(
            f"Invalid Kubernetes channel {channel!r}; specify a version track."
        )
    return normalized


def _manifest_k8s_settings(manifest: Manifest | dict) -> dict:
    """Read explicit Kubernetes settings from a loaded or raw manifest."""
    if isinstance(manifest, Manifest):
        charm = manifest.core.software.charms.get("k8s")
        return charm.model_dump() if charm is not None else {}
    return manifest.get("core", {}).get("software", {}).get("charms", {}).get("k8s", {})


class K8STrackGuard:
    """Resolve and validate requests against an immutable cluster record.

    Untracked deployments can adopt their observed track when stored
    deployment data agrees. Recorded tracks reject subsequent Juju drift.
    Serialize first apply and track recording with a separate lock from the
    Terraform backend so a retry cannot choose a different track mid-apply.
    """

    def __init__(self, client: Client, jhelper: JujuHelper | None, model: str):
        self.client = client
        self.jhelper = jhelper
        self.model = model

    @tenacity.retry(
        wait=tenacity.wait_fixed(1),
        stop=tenacity.stop_after_delay(60),
        retry=tenacity.retry_if_exception_type(TerraformPlanLockConflictException),
        reraise=True,
    )
    def _lock(self, lock: dict) -> None:
        self.client.cluster.lock_terraform_plan(_TRACK_LOCK, lock)

    def validate_manifest(self, manifest: Manifest | dict) -> None:
        """Check the manifest's Kubernetes selection against the recorded track."""
        settings = _manifest_k8s_settings(manifest)
        self.resolve(
            settings.get("channel"),
            settings.get("revision"),
        )

    def resolve(
        self,
        channel: str | None,
        revision: int | None = None,
        *,
        persist: bool = False,
    ) -> str:
        """Validate before writing; optionally initialize the track exactly once."""
        if not persist:
            return self._resolve(channel, revision, persist=False)

        with self._locked():
            return self._resolve(channel, revision, persist=True)

    @contextmanager
    def terraform_apply(self) -> Iterator[None]:
        """Hold the track stable through Terraform apply and record its success."""
        with self._locked():
            yield
            if self._recorded_track() is None:
                self._resolve(None, None, persist=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock = {
            "ID": str(uuid.uuid4()),
            "Operation": "Record Kubernetes deployment track",
            "Who": self.model,
        }
        self._lock(lock)
        try:
            yield
        finally:
            self.client.cluster.unlock_terraform_plan(_TRACK_LOCK, lock)

    def _legacy_tfvars(self) -> dict | None:
        try:
            return read_config(self.client, "TerraformVarsK8S")
        except ConfigItemNotFoundException:
            return None

    def _existing_track(self, observed_track: str | None) -> str | None:
        bootstrapped = self.client.cluster.check_sunbeam_bootstrapped() in (
            True,
            "True",
        )
        if (
            observed_track is not None
            or bootstrapped
            or "k8s-plan" in self.client.cluster.list_terraform_plans()
        ):
            if self.jhelper is None:
                raise CharmChannelError(
                    "Cannot verify the deployed Kubernetes track without Juju."
                )
            return self._legacy_track(observed_track, self._legacy_tfvars())
        return None

    def _new_track(self, requested_track: str | None) -> str:
        track = requested_track or channel_track(K8S_CHANNEL)
        if tuple(map(int, track.split("."))) < tuple(
            map(int, K8S_MIN_SUPPORTED_TRACK.split("."))
        ):
            raise CharmChannelError(
                f"Kubernetes track {track!r} is below the "
                f"minimum supported track {K8S_MIN_SUPPORTED_TRACK!r}."
            )
        return track

    def _legacy_track(self, observed_track: str | None, tfvars: dict | None) -> str:
        saved_channel = (tfvars or {}).get("k8s_channel")
        saved_track = (
            channel_track(_k8s_channel(saved_channel))
            if saved_channel is not None
            else None
        )
        track = observed_track or saved_track
        if track is None:
            raise CharmChannelError(
                "Cannot determine the existing Kubernetes track from Juju "
                "or the saved Terraform channel."
            )
        if saved_track is not None and saved_track != track:
            raise CharmChannelError(
                f"Cannot record Kubernetes track {track!r}: saved Terraform "
                f"channel {saved_channel!r} is on a different track. "
                "Reconcile the deployed channel with the saved deployment "
                "configuration before continuing."
            )

        return track

    def _check_revision(self, revision: int, track: str) -> None:
        # Charmhub queries do not require a bootstrapped controller.
        jhelper = self.jhelper if self.jhelper is not None else JujuHelper
        try:
            channels = jhelper.get_charm_channels_for_revision("k8s", revision)
        except JujuException as e:
            raise CharmChannelError(
                f"Cannot verify Kubernetes revision {revision}: {e}"
            ) from e
        validate_revision_track(
            revision, channels, track, "Kubernetes", require_published=True
        )

    def _observed_application(self) -> AppStatus | None:
        if self.jhelper is not None:
            try:
                return self.jhelper.get_application("k8s", self.model)
            except (ApplicationNotFoundException, ModelNotFoundException):
                pass
            except JujuException as e:
                raise CharmChannelError(
                    f"Cannot verify the deployed Kubernetes track: {e}"
                ) from e
        return None

    def _recorded_track(self) -> str | None:
        try:
            record = read_config(self.client, K8S_TRACK_CONFIG_KEY)
        except ConfigItemNotFoundException:
            return None
        track = record.get("track") if isinstance(record, dict) else None
        if not isinstance(track, str) or not _VERSION_TRACK.fullmatch(track):
            raise CharmChannelError("Invalid Kubernetes deployment track record.")
        return track

    def _resolve(
        self,
        channel: str | None,
        revision: int | None,
        *,
        persist: bool,
    ) -> str:
        requested_channel = _k8s_channel(channel) if channel is not None else None
        requested_track = (
            channel_track(requested_channel) if requested_channel else None
        )
        recorded_track = self._recorded_track()
        app = self._observed_application()

        observed_track = None
        if app is not None:
            if not app.charm_channel:
                raise CharmChannelError(
                    "Juju does not report the Kubernetes charm channel."
                )
            observed_track = channel_track(_k8s_channel(app.charm_channel))

        existing = None
        if recorded_track is None:
            existing = self._existing_track(observed_track)
            track = existing or self._new_track(requested_track)
        else:
            track = recorded_track
            if self.jhelper is None and (
                self._legacy_tfvars() is not None
                or self.client.cluster.check_sunbeam_bootstrapped() in (True, "True")
            ):
                raise CharmChannelError(
                    "Cannot verify the deployed Kubernetes track without Juju."
                )

        if app is not None and observed_track != track:
            raise CharmChannelError(
                f"Kubernetes track changed outside Sunbeam: deployment track "
                f"{track!r}, observed Juju channel {app.charm_channel!r}. "
                "Track changes are not supported; "
                "a manifest cannot adopt this change."
            )

        if requested_track is not None and requested_track != track:
            raise CharmChannelError(
                f"Kubernetes channel {channel!r} changes deployment track {track!r} "
                f"to {requested_track!r}. Track changes are not supported; "
                "only risk or branch changes within the deployment track are allowed."
            )

        if revision is not None and (app is None or app.charm_rev != revision):
            self._check_revision(revision, track)

        if persist and recorded_track is None and existing:
            update_config(self.client, K8S_TRACK_CONFIG_KEY, {"track": track})

        if requested_channel is not None:
            return requested_channel
        return app.charm_channel if app is not None else f"{track}/stable"
