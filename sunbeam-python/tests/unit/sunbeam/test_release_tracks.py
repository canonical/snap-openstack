# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for RELEASE_TRACKS, release detection, and SLURP hop validation."""

from sunbeam.versions import (
    DEFAULT_RELEASE,
    RELEASE_TRACKS,
    SLURP_HOPS,
    detect_deployed_release,
    get_release_tracks,
    is_valid_hop,
)


class TestReleaseTracks:
    def test_all_releases_present(self):
        assert "2024.1" in RELEASE_TRACKS
        assert "2025.1" in RELEASE_TRACKS
        assert "2026.1" in RELEASE_TRACKS

    def test_each_track_has_required_fields(self):
        required = [
            "name",
            "openstack_channel",
            "microceph_channel",
            "microovn_channel",
            "mysql_channel",
            "rabbitmq_channel",
            "vault_channel",
            "consul_channel",
        ]
        for release, tracks in RELEASE_TRACKS.items():
            for field in required:
                assert field in tracks, f"{release} missing {field}"

    def test_release_names(self):
        assert RELEASE_TRACKS["2024.1"]["name"] == "caracal"
        assert RELEASE_TRACKS["2025.1"]["name"] == "epoxy"
        assert RELEASE_TRACKS["2026.1"]["name"] == "gazpacho"

    def test_get_release_tracks(self):
        tracks = get_release_tracks("2025.1")
        assert tracks["openstack_channel"] == "2025.1/stable"

    def test_get_release_tracks_raises_on_unknown(self):
        import pytest

        with pytest.raises(KeyError):
            get_release_tracks("1999.1")


class TestSlurpHops:
    def test_valid_hops(self):
        assert is_valid_hop("2024.1", "2025.1") is True
        assert is_valid_hop("2025.1", "2026.1") is True

    def test_invalid_hops(self):
        assert is_valid_hop("2024.1", "2026.1") is False
        assert is_valid_hop("2025.1", "2024.1") is False
        assert is_valid_hop("1999.1", "2025.1") is False

    def test_slurp_hops_set(self):
        assert ("2024.1", "2025.1") in SLURP_HOPS
        assert ("2025.1", "2026.1") in SLURP_HOPS


class TestDetectSnapRelease:
    def test_returns_default_when_unknown(self):
        # detect_snap_release falls back to DEFAULT_RELEASE for unknown
        # snap version strings.
        assert DEFAULT_RELEASE in RELEASE_TRACKS

    def test_parses_snap_version_string(self, monkeypatch):
        import sunbeam.versions as versions

        class FakeSnap:
            version = "2026.1-abc123"

        monkeypatch.setattr(versions, "DEFAULT_RELEASE", "2026.1")
        monkeypatch.setattr("snaphelpers.Snap", lambda: FakeSnap())
        assert versions.detect_snap_release() == "2026.1"

    def test_falls_back_on_exception(self, monkeypatch):
        import sunbeam.versions as versions

        def _boom():
            raise RuntimeError("no snap")

        monkeypatch.setattr(versions, "DEFAULT_RELEASE", "2026.1")
        monkeypatch.setattr("snaphelpers.Snap", _boom)
        assert versions.detect_snap_release() == "2026.1"

    def test_falls_back_on_unknown_version(self, monkeypatch):
        import sunbeam.versions as versions

        class FakeSnap:
            version = "99.9-xyz"

        monkeypatch.setattr(versions, "DEFAULT_RELEASE", "2026.1")
        monkeypatch.setattr("snaphelpers.Snap", lambda: FakeSnap())
        assert versions.detect_snap_release() == "2026.1"


class TestDetectDeployedRelease:
    def test_detects_2025_1(self):
        channels = {
            "keystone-k8s": "2025.1/stable",
            "nova-k8s": "2025.1/stable",
            "glance-k8s": "2025.1/stable",
        }
        assert detect_deployed_release(channels) == "2025.1"

    def test_detects_2024_1(self):
        channels = {
            "keystone-k8s": "2024.1/stable",
            "nova-k8s": "2024.1/stable",
        }
        assert detect_deployed_release(channels) == "2024.1"

    def test_returns_none_for_unknown_channels(self):
        channels = {
            "keystone-k8s": "1999.1/stable",
            "nova-k8s": "1999.1/stable",
        }
        assert detect_deployed_release(channels) is None

    def test_returns_none_for_empty(self):
        assert detect_deployed_release({}) is None

    def test_requires_at_least_two_matching(self):
        # Only one charm matching is not enough — could be a stale
        # channel on one app during an upgrade
        channels = {"keystone-k8s": "2025.1/stable"}
        assert detect_deployed_release(channels) is None

    def test_detects_2026_1(self):
        channels = {
            "keystone-k8s": "2026.1/stable",
            "nova-k8s": "2026.1/stable",
            "neutron-k8s": "2026.1/stable",
        }
        assert detect_deployed_release(channels) == "2026.1"
