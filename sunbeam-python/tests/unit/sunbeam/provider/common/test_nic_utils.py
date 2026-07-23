# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam.provider.common import nic_utils


class TestWhitelistRemoteManagedVf:
    def _vf(self, pci_address="0000:41:00.3", vendor="0x15b3", product="0x101e"):
        return {
            "name": "ens1f0v0",
            "pci_address": pci_address,
            "vendor_id": vendor,
            "product_id": product,
            "pf_pci_address": "0000:41:00.0",
            "sriov_available": False,
        }

    def test_builds_remote_managed_spec_with_vf_address_and_null_physnet(self):
        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {}

        nic_utils.whitelist_remote_managed_vf(
            "compute-1", self._vf(), pci_whitelist, excluded_devices, "physnet1"
        )

        assert pci_whitelist == [
            {
                "address": "0000:41:00.3",
                "vendor_id": "15b3",
                "product_id": "101e",
                "physical_network": None,
                "remote_managed": "true",
            }
        ]

    def test_keeps_distinct_vf_addresses(self):
        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {}

        for func in (3, 4, 5, 6):
            nic_utils.whitelist_remote_managed_vf(
                "compute-1",
                self._vf(pci_address=f"0000:41:00.{func}"),
                pci_whitelist,
                excluded_devices,
                "physnet1",
            )

        assert [spec["address"] for spec in pci_whitelist] == [
            "0000:41:00.3",
            "0000:41:00.4",
            "0000:41:00.5",
            "0000:41:00.6",
        ]

    def test_ignores_maas_physnet_for_remote_managed_vfs(self):
        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {}

        nic_utils.whitelist_remote_managed_vf(
            "compute-1", self._vf(), pci_whitelist, excluded_devices, "physnet1"
        )

        assert pci_whitelist[0]["physical_network"] is None

    def test_removes_vf_from_exclusion_list(self):
        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {"compute-1": ["0000:41:00.3"]}

        nic_utils.whitelist_remote_managed_vf(
            "compute-1", self._vf(), pci_whitelist, excluded_devices, "physnet1"
        )

        assert "0000:41:00.3" not in excluded_devices["compute-1"]
