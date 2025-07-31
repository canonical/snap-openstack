# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# This module can be used to match Nova PCI whitelist specs.
# Based on https://github.com/openstack/nova/blob/master/nova/pci/devspec.py.

import logging
import re
import typing

LOG = logging.getLogger(__name__)

ANY = "*"


def _parse_hex(value: str, fmt: str) -> str:
    value = value.strip() or ANY
    if value == ANY:
        return value
    try:
        return fmt % int(value, 16)
    except ValueError:
        raise Exception("Invalid hexadecimal value: %s" % value)


_parse_domain = lambda x: _parse_hex(x, "%04x")  # noqa: E731
_parse_bus = lambda x: _parse_hex(x, "%02x")  # noqa: E731
_parse_slot = lambda x: _parse_hex(x, "%02x")  # noqa: E731
_parse_function = lambda x: _parse_hex(x, "%01x")  # noqa: E731
_parse_vendor = lambda x: _parse_hex(x, "%04x")  # noqa: E731
_parse_product = lambda x: _parse_hex(x, "%04x")  # noqa: E731


class PciAddress:
    """A fully qualified PCI address that can be matched against address specs."""

    def __init__(self, address: str):
        try:
            domain_bus_slot, function = address.split(".")
            domain, bus, slot = domain_bus_slot.split(":")
            self.domain = _parse_domain(domain)
            self.bus = _parse_bus(bus)
            self.slot = _parse_slot(slot)
            self.function = _parse_function(function)
        except (KeyError, ValueError):
            raise Exception("Invalid address format: %s" % address)


class PciAddressRegexSpec:
    """PCI address spec using regex-style patterns for each address field."""

    def __init__(self, address: dict):
        try:
            self.domain_re = re.compile(address.get("domain", ".*"))
            self.bus_re = re.compile(address.get("bus", ".*"))
            self.slot_re = re.compile(address.get("slot", ".*"))
            self.function_re = re.compile(address.get("function", ".*"))
        except re.error:
            raise Exception("Invalid address regexes: %s" % address)

    def match(self, address: PciAddress) -> bool:
        """Regex match PCI address."""
        return all(
            (
                self.domain_re.match(address.domain),
                self.bus_re.match(address.bus),
                self.slot_re.match(address.slot),
                self.function_re.match(address.function),
            )
        )


class PciAddressGlobSpec:
    """PCI address spec using glob-style patterns."""

    def __init__(self, address: str):
        self.domain = ANY
        self.bus = ANY
        self.slot = ANY
        self.function = ANY

        domain_bus_slot_str, _, function = address.partition(".")
        if domain_bus_slot_str:
            domain_bus_slot = domain_bus_slot_str.split(":")
            if len(domain_bus_slot) > 3:
                raise Exception("Invalid address format: %s" % address)
            # Allow partial addresses.
            domain, bus, slot = [ANY] * (3 - len(domain_bus_slot)) + domain_bus_slot
            self.domain = _parse_domain(domain)
            self.bus = _parse_bus(bus)
            self.slot = _parse_slot(slot)
        if function:
            self.function = _parse_function(function)

    def match(self, address: PciAddress) -> bool:
        """Glob match PCI address."""
        return all(
            (
                self.domain in (ANY, address.domain),
                self.bus in (ANY, address.bus),
                self.slot in (ANY, address.slot),
                self.function in (ANY, address.function),
            )
        )


class PciDeviceSpec:
    """Match Nova PCI device specs."""

    def __init__(self, dev_spec: dict[str, str]):
        self.vendor_id = _parse_vendor(dev_spec.get("vendor_id") or ANY)
        self.product_id = _parse_product(dev_spec.get("product_id") or ANY)

        address = dev_spec.get("address") or "*:*:*.*"
        if isinstance(address, str):
            self.address_spec = PciAddressGlobSpec(address)
        elif isinstance(address, dict):
            self.address_spec = PciAddressRegexSpec(address)
        else:
            raise Exception("Invalid address format: %s" % address)

    def _address_match(self, address: str, parent_address: str | None) -> bool:
        # Whitelist SR-IOV VFs if the parent PF address matches.
        if parent_address and self.address_spec.match(PciAddress(parent_address)):
            return True

        return self.address_spec.match(PciAddress(address))

    def match(self, dev_dict: dict[str, typing.Any]) -> bool:
        """Match a PCI device against this spec."""
        return all(
            (
                self.vendor_id in (ANY, dev_dict["vendor_id"]),
                self.product_id in (ANY, dev_dict["product_id"]),
                self._address_match(dev_dict["address"], dev_dict.get("parent_addr")),
            )
        )
