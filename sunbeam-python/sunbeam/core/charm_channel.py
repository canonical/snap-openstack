# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Channel and revision checks shared by charm deployment and refresh."""

import re
from collections.abc import Sequence

import click

_RISKS = {"stable", "candidate", "beta", "edge"}
_CHANNEL = re.compile(r"([^/\s]+)(?:/(stable|candidate|beta|edge)(?:/[^/\s]+)?)?")


class CharmChannelError(click.ClickException):
    """A charm selection violates its channel policy."""


def channel_track(channel: str) -> str:
    """Return the track of a Juju channel."""
    return channel.split("/", 1)[0]


def normalize_explicit_channel(channel: str, software: str) -> str:
    """Validate track[/risk[/branch]] and use stable when risk is omitted."""
    if (
        not isinstance(channel, str)
        or not _CHANNEL.fullmatch(channel)
        or channel_track(channel) in _RISKS
    ):
        raise CharmChannelError(
            f"Invalid {software} channel {channel!r}; specify track[/risk[/branch]]."
        )
    return channel if "/" in channel else f"{channel}/stable"


def validate_revision_track(
    revision: int,
    channels: Sequence[str],
    track: str,
    software: str,
    *,
    require_published: bool,
) -> None:
    """Check track membership against the channels publishing a revision."""
    if not channels and not require_published:
        return
    if any(channel_track(channel) == track for channel in channels):
        return
    raise CharmChannelError(
        f"Cannot verify {software} revision {revision} on deployment track "
        f"{track!r}. Published channels: {', '.join(channels) or 'unknown'}. "
        "Track changes are not supported."
    )
