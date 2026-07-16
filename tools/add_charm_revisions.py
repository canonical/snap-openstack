#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Add charm revisions to a sunbeam manifest file by querying the Charmhub API.

Usage:
    python3 tools/add_charm_revisions.py <input-manifest.yml> [output-manifest.yml]

If no output path is given, the result is printed to stdout.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml


def fetch_revision(charm: str, channel: str) -> int | None:
    """Return the revision number for *charm* at *channel*, or None on failure."""
    url = f"https://api.charmhub.io/v2/charms/info/{charm}?fields=channel-map"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "add-charm-revisions/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            if urllib.parse.urlparse(final_url).scheme not in ("https",):
                print(
                    f"  WARNING: Insecure redirect to {final_url!r} for {charm!r}",
                    file=sys.stderr,
                )
                return None
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"  WARNING: could not fetch info for {charm!r}: {exc}", file=sys.stderr)
        return None

    channel_map = data.get("channel-map", [])

    # The Charmhub API omits the "latest/" prefix in channel names.
    normalised = channel.removeprefix("latest/")

    for entry in channel_map:
        ch_name = entry.get("channel", {}).get("name", "")
        if ch_name == channel or ch_name == normalised:
            return entry.get("revision", {}).get("revision")

    print(
        f"  WARNING: channel {channel!r} not found in channel-map for {charm!r}",
        file=sys.stderr,
    )
    return None


def collect_charms(obj: dict) -> list[tuple[str, str]]:
    """Walk the manifest and return a flat list of (charm_name, channel) pairs."""
    pairs: list[tuple[str, str]] = []

    def _walk(node):
        if not isinstance(node, dict):
            return
        charms = node.get("charms")
        if isinstance(charms, dict):
            for name, charm in charms.items():
                if (
                    isinstance(charm, dict)
                    and "channel" in charm
                    and "revision" not in charm
                ):
                    pairs.append((name, charm["channel"]))
        for value in node.values():
            if isinstance(value, dict):
                _walk(value)

    _walk(obj)
    return pairs


def inject_revisions(obj: dict, revisions: dict[tuple[str, str], int | None]) -> dict:
    """Return a new structure with *revision* inserted after each *channel* key."""
    if not isinstance(obj, dict):
        return obj

    charms = obj.get("charms")
    if isinstance(charms, dict):
        new_charms = {}
        for name, charm in charms.items():
            if (
                isinstance(charm, dict)
                and "channel" in charm
                and "revision" not in charm
            ):
                new_charm: dict = {}
                for key, value in charm.items():
                    new_charm[key] = value
                    if key == "channel":
                        rev = revisions.get((name, value))
                        if rev is not None:
                            new_charm["revision"] = rev
                new_charms[name] = new_charm
            else:
                new_charms[name] = charm
        obj = {k: (new_charms if k == "charms" else v) for k, v in obj.items()}

    return {
        k: inject_revisions(v, revisions) if isinstance(v, dict) else v
        for k, v in obj.items()
    }


def process_manifest(input_path: str, output_path: str | None) -> None:
    """Read the manifest, fetch revisions, and write the updated manifest."""
    with open(input_path, "r") as fh:
        data = yaml.safe_load(fh)

    pairs = collect_charms(data)
    if not pairs:
        print("No charm entries found in the manifest.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate by (charm, channel) so we only hit the API once per pair.
    unique_pairs: dict[tuple[str, str], int | None] = {}
    for name, channel in pairs:
        key = (name, channel)
        if key not in unique_pairs:
            print(f"Fetching revision for {name!r} @ {channel!r} ...", file=sys.stderr)
            unique_pairs[key] = fetch_revision(name, channel)

    updated = sum(1 for v in unique_pairs.values() if v is not None)
    skipped = sum(1 for v in unique_pairs.values() if v is None)

    data = inject_revisions(data, unique_pairs)

    print(
        f"\nDone: {updated} charm(s) updated, {skipped} skipped.",
        file=sys.stderr,
    )

    output = yaml.dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True
    )

    if output_path:
        with open(output_path, "w") as fh:
            fh.write(output)
        print(f"Written to {output_path}", file=sys.stderr)
    else:
        sys.stdout.write(output)


def main() -> None:
    """Main function to add charm revisions to a Sunbeam manifest."""
    parser = argparse.ArgumentParser(
        description="Add Charmhub revisions to a sunbeam manifest YAML file."
    )
    parser.add_argument("input", help="Path to the input manifest YAML file.")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Path to write the updated manifest (default: stdout).",
    )
    args = parser.parse_args()
    process_manifest(args.input, args.output)


if __name__ == "__main__":
    main()
