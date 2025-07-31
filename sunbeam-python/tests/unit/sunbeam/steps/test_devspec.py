# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import pytest

from sunbeam import devspec


class TestDevspec:
    """Contains tests for the devspec matchers."""

    @pytest.mark.parametrize(
        "device_spec, matched_device, expect_match",
        [
            (
                {
                    "vendor_id": "8086",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "vendor_id": "8087",
                    "product_id": "aaff",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "vendor_id": "8086",
                    "product_id": "aa",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "address": "0000:1b:10.6",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "address": "0000:1b:10.7",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "address": "0000:1b:*.*",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "address": "0000:1f:*.*",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "address": ":1b:",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "address": ":1f:",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "address": {
                        "domain": ".*",
                        "bus": "1b",
                        "slot": "10",
                        "function": "[2-7]",
                    },
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                True,
            ),
            (
                {
                    "address": {
                        "domain": ".*",
                        "bus": "1b",
                        "slot": "10",
                        "function": "[0-4]",
                    },
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                },
                False,
            ),
            (
                {
                    "address": "0000:1b:10.0",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                    "parent_addr": "0000:1b:10.0",
                },
                True,
            ),
            (
                {
                    "address": "0000:1b:10.0",
                },
                {
                    "vendor_id": "8086",
                    "product_id": "aaff",
                    "address": "0000:1b:10.6",
                    "parent_addr": "0000:1b:10.1",
                },
                False,
            ),
        ],
    )
    def test_pci_device_match(
        self, device_spec: dict, matched_device: dict, expect_match: bool
    ):
        spec = devspec.PciDeviceSpec(device_spec)
        match = spec.match(matched_device)
        assert expect_match == match
