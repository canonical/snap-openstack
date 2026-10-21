# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import typing

from rich.console import Console

from sunbeam.core.common import LB_INGRESS_RESERVED_ADDRESSES
from sunbeam.core.questions import QuestionBank, show_questions

OPENSTACK_MODEL = "openstack"
REGION_CONFIG_KEY = "Region"
DEFAULT_REGION = "RegionOne"
ENDPOINTS_CONFIG_KEY = "Endpoints"

INGRESS_ENDPOINT_TYPES = ["internal", "public", "rgw"]


INGRESS_ENDPOINT_TERRAFORM_MAP = {
    "internal": "traefik-config",
    "public": "traefik-public-config",
    "rgw": "traefik-rgw-config",
}


def ip_in_reserved_lb_prefix(
    ip_address: (
        ipaddress.IPv4Address | ipaddress.IPv6Address
    ),
    lb_range: (
        tuple[
            ipaddress.IPv4Address | ipaddress.IPv6Address,
            ipaddress.IPv4Address | ipaddress.IPv6Address,
        ]
        | ipaddress.IPv4Network
        | ipaddress.IPv6Network
    ),
    reserved: int = LB_INGRESS_RESERVED_ADDRESSES,
) -> bool:
    """Return whether ``ip_address`` is within the reserved low-end of a pool.

    The first ``reserved`` addresses of a LoadBalancer pool are auto-allocated
    to internal services, so an operator-configured endpoint IP must not use
    them. ``lb_range`` may be either a CIDR/network or a ``(start, end)`` tuple
    of addresses; both are normalised to their numeric ``[start, end]`` span
    and compared as integers.
    """
    if isinstance(lb_range, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        if ip_address.version != lb_range.version:
            return False
        start = int(lb_range.network_address)
        end = int(lb_range.broadcast_address)
    else:
        start_ip, end_ip = lb_range
        if ip_address.version != start_ip.version:
            return False
        start, end = int(start_ip), int(end_ip)

    # The reserved region is [start, min(start + reserved - 1, end)]: it can
    # never extend past the last address of the range, so a pool smaller than
    # ``reserved`` is fully reserved.
    return start <= int(ip_address) <= min(start + reserved - 1, end)


def get_ingress_endpoint_key(endpoint_type: str) -> str:
    """Get the config key for an ingress endpoint type."""
    return f"ingress-{endpoint_type}"


def generate_endpoint_preseed_questions(
    endpoint_questions_func: typing.Callable[[str], dict],
    console: Console,
    variables: dict,
) -> list[str]:
    """Generate preseed questions for endpoint configuration.

    Args:
        endpoint_questions_func: Function that takes endpoint type and returns questions
        console: Rich console for output
        variables: Previous answers/variables

    Returns:
        List of preseed content lines
    """
    preseed_content = ["    endpoints:"]

    for endpoint in INGRESS_ENDPOINT_TYPES:
        questions = endpoint_questions_func(endpoint)
        questions = {
            k: v for k, v in questions.items() if not k.startswith("configure")
        }
        endpoint_bank = QuestionBank(
            questions=questions,
            console=console,
            previous_answers=variables.get(get_ingress_endpoint_key(endpoint), {}),
        )
        preseed_content.extend(
            show_questions(
                endpoint_bank,
                section=get_ingress_endpoint_key(endpoint),
                section_description=f"{endpoint.title()} Endpoint",
                initial_indent=6,
            )
        )

    return preseed_content
