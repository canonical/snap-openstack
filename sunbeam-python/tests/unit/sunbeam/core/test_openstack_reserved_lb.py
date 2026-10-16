# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import pytest

from sunbeam.core.openstack import (
    LB_INGRESS_RESERVED_ADDRESSES,
    ip_in_reserved_lb_prefix,
)


class TestIPInReservedLBPrefix:
    """Tests for the reserved low-end LoadBalancer prefix check."""

    @pytest.mark.parametrize(
        "lb_range, ip",
        [
            ("10.51.88.201-10.51.88.220", "10.51.88.201"),
            ("10.51.88.201-10.51.88.220", "10.51.88.210"),
            ("172.21.4.0/24", "172.21.4.0"),
            ("172.21.4.0/24", "172.21.4.9"),
        ],
    )
    def test_ip_in_reserved_prefix(self, lb_range, ip):
        from sunbeam.core.common import parse_ip_range_or_cidr

        parsed = parse_ip_range_or_cidr(lb_range)
        assert ip_in_reserved_lb_prefix(__import__("ipaddress").ip_address(ip), parsed)

    @pytest.mark.parametrize(
        "lb_range, ip",
        [
            ("10.51.88.201-10.51.88.220", "10.51.88.211"),
            ("10.51.88.201-10.51.88.220", "10.51.88.220"),
            ("172.21.4.0/24", "172.21.4.10"),
            ("172.21.4.0/24", "172.21.4.250"),
        ],
    )
    def test_ip_not_in_reserved_prefix(self, lb_range, ip):
        from sunbeam.core.common import parse_ip_range_or_cidr

        parsed = parse_ip_range_or_cidr(lb_range)
        assert not ip_in_reserved_lb_prefix(
            __import__("ipaddress").ip_address(ip), parsed
        )

    def test_reserved_default_is_ten(self):
        assert LB_INGRESS_RESERVED_ADDRESSES == 10

    def test_ip_out_of_range_not_reserved(self):
        import ipaddress

        from sunbeam.core.common import parse_ip_range_or_cidr

        parsed = parse_ip_range_or_cidr("172.21.4.0-172.21.4.19")
        assert not ip_in_reserved_lb_prefix(
            ipaddress.ip_address("172.21.4.30"), parsed
        )

    def test_custom_reserved_count(self):
        import ipaddress

        from sunbeam.core.common import parse_ip_range_or_cidr

        parsed = parse_ip_range_or_cidr("172.21.4.0-172.21.4.19")
        # With reserved=2 only the first two addresses are reserved.
        assert ip_in_reserved_lb_prefix(
            ipaddress.ip_address("172.21.4.1"), parsed, reserved=2
        )
        assert not ip_in_reserved_lb_prefix(
            ipaddress.ip_address("172.21.4.2"), parsed, reserved=2
        )

    def test_version_mismatch_not_reserved(self):
        import ipaddress

        from sunbeam.core.common import parse_ip_range_or_cidr

        parsed = parse_ip_range_or_cidr("172.21.4.0-172.21.4.19")
        assert not ip_in_reserved_lb_prefix(
            ipaddress.ip_address("2001:db8::1"), parsed
        )


class TestValidateCidrOrIPRangeMinSize:
    """Tests for the minimum-size guard on LoadBalancer ranges."""

    @pytest.mark.parametrize(
        "ip_range",
        [
            "11.22.33.101-11.22.33.125",  # exactly 25 addresses
            "198.51.100.0/24",            # 256 addresses
            "192.168.10.200-192.168.10.215",  # 16 addresses
        ],
    )
    def test_range_with_enough_addresses_accepted(self, ip_range):
        from sunbeam.core.common import validate_cidr_or_ip_range

        validate_cidr_or_ip_range(ip_range)

    @pytest.mark.parametrize(
        "ip_range",
        [
            "11.22.33.101-11.22.33.110",  # exactly 10 -> accepted boundary
            "11.22.33.101-11.22.33.111",
        ],
    )
    def test_range_at_least_reserved_enough(self, ip_range):
        from sunbeam.core.common import validate_cidr_or_ip_range

        validate_cidr_or_ip_range(ip_range)

    @pytest.mark.parametrize(
        "ip_range",
        [
            "11.22.33.101-11.22.33.109",  # 9 addresses
            "198.51.100.0-198.51.100.3",  # 4 addresses
            "203.0.113.96/30",            # 4 addresses
        ],
    )
    def test_range_too_small_rejected(self, ip_range):
        from sunbeam.core.common import validate_cidr_or_ip_range

        with pytest.raises(ValueError, match="must contain at least"):
            validate_cidr_or_ip_range(ip_range)

    @pytest.mark.parametrize(
        "ranges",
        [
            "192.168.10.200-192.168.10.215,10.5.5.101-10.5.5.120",
            "203.0.113.0/24,198.51.100.0/28",  # second entry has 16 addresses
        ],
    )
    def test_multi_range_accepted(self, ranges):
        from sunbeam.core.common import validate_cidr_or_ip_ranges

        validate_cidr_or_ip_ranges(ranges)

    def test_multi_range_rejected_when_any_too_small(self):
        from sunbeam.core.common import validate_cidr_or_ip_ranges

        with pytest.raises(ValueError, match="must contain at least"):
            validate_cidr_or_ip_ranges("192.168.10.200-192.168.10.215,10.5.5.101-10.5.5.103")


class TestLocalEndpointsConfigurationStepReserved:
    """Tests for the local endpoints step rejecting reserved IPs."""

    def test_validate_endpoint_rejects_reserved_ip(self, mocker):
        from sunbeam.core.common import parse_ip_range_or_cidr
        from sunbeam.provider.local.steps import (
            LocalEndpointsConfigurationStep,
        )

        step = LocalEndpointsConfigurationStep.__new__(
            LocalEndpointsConfigurationStep
        )
        step.loadbalancer_range = parse_ip_range_or_cidr("172.19.3.50-172.19.3.80")

        with pytest.raises(ValueError, match="reserved"):
            step._validate_endpoint("internal", "172.19.3.55")

    def test_validate_endpoint_accepts_non_reserved_in_range(self, mocker):
        from sunbeam.core.common import parse_ip_range_or_cidr
        from sunbeam.provider.local.steps import (
            LocalEndpointsConfigurationStep,
        )

        step = LocalEndpointsConfigurationStep.__new__(
            LocalEndpointsConfigurationStep
        )
        step.loadbalancer_range = parse_ip_range_or_cidr("172.19.3.50-172.19.3.80")

        assert step._validate_endpoint("internal", "172.19.3.75")

    def test_validate_endpoint_rejects_out_of_range(self, mocker):
        from sunbeam.core.common import parse_ip_range_or_cidr
        from sunbeam.provider.local.steps import (
            LocalEndpointsConfigurationStep,
        )

        step = LocalEndpointsConfigurationStep.__new__(
            LocalEndpointsConfigurationStep
        )
        step.loadbalancer_range = parse_ip_range_or_cidr("172.19.3.50-172.19.3.80")

        assert not step._validate_endpoint("internal", "172.19.3.90")
