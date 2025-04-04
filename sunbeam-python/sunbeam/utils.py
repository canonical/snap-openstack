# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import collections.abc
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import string
import sys
import typing
from functools import update_wrapper
from pathlib import Path

import click
import netifaces  # type: ignore [import-untyped]
from pyroute2 import NDB  # type: ignore [import-untyped]

from sunbeam.core.common import SunbeamException

LOG = logging.getLogger(__name__)
LOCAL_ACCESS = "local"
REMOTE_ACCESS = "remote"
IPVANYNETWORK_UNSET = "0.0.0.0/0"


def get_hypervisor_hostname() -> str:
    """Get FQDN as per libvirt."""
    # Use same logic used by libvirt
    # https://github.com/libvirt/libvirt/blob/a5bf2c4bf962cfb32f9137be5f0ba61cdd14b0e7/src/util/virutil.c#L406
    hostname = socket.gethostname()
    if "." in hostname:
        return hostname

    addrinfo = socket.getaddrinfo(
        hostname, None, family=socket.AF_UNSPEC, flags=socket.AI_CANONNAME
    )
    for addr in addrinfo:
        fqdn = addr[3]
        if fqdn and fqdn != "localhost":
            return fqdn

    return hostname


def get_fqdn(cidr: str | None = None) -> str:
    """Get FQDN of the machine."""
    # If the fqdn returned by this function and from libvirt are different,
    # the hypervisor name and the one registered in OVN will be different
    # which leads to port binding errors,
    # see https://bugs.launchpad.net/snap-openstack/+bug/2023931

    fqdn = get_hypervisor_hostname()
    if "." in fqdn:
        return fqdn

    # Deviation from libvirt logic
    # Try to get fqdn from IP address as a last resort
    if cidr:
        ip = get_local_ip_by_cidr(cidr)
    else:
        ip = get_local_ip_by_default_route()
    try:
        fqdn = socket.getfqdn(socket.gethostbyaddr(ip)[0])
        if fqdn != "localhost":
            return fqdn
    except Exception as e:
        LOG.debug("Ignoring error in getting FQDN")
        LOG.debug(e, exc_info=True)

    # return hostname if fqdn is localhost
    return socket.gethostname()


def _get_default_gw_iface_fallback() -> str | None:
    """Returns the default gateway interface.

    Parses the /proc/net/route table to determine the interface with a default
    route. The interface with the default route will have a destination of 0x000000,
    a mask of 0x000000 and will have flags indicating RTF_GATEWAY and RTF_UP.

    :return Optional[str, None]: the name of the interface the default gateway or
            None if one cannot be found.
    """
    # see include/uapi/linux/route.h in kernel source for more explanation
    RTF_UP = 0x1  # noqa - route is usable
    RTF_GATEWAY = 0x2  # noqa - destination is a gateway

    iface = None
    with open("/proc/net/route", "r") as f:
        contents = [line.strip() for line in f.readlines() if line.strip()]
        logging.debug(contents)

        entries: list[dict[str, str]] = []
        # First line is a header line of the table contents. Note, we skip blank entries
        # by default there's an extra column due to an extra \t character for the table
        # contents to line up. This is parsing the /proc/net/route and creating a set of
        # entries. Each entry is a dict where the keys are table header and the values
        # are the values in the table rows.
        header = [col.strip().lower() for col in contents[0].split("\t") if col]
        for row in contents[1:]:
            cells = [col.strip() for col in row.split("\t") if col]
            entries.append(dict(zip(header, cells)))

        def is_up(flags: str) -> bool:
            return int(flags, 16) & RTF_UP == RTF_UP

        def is_gateway(flags: str) -> bool:
            return int(flags, 16) & RTF_GATEWAY == RTF_GATEWAY

        # Check each entry to see if it has the default gateway. The default gateway
        # will have destination and mask set to 0x00, will be up and is noted as a
        # gateway.
        for entry in entries:
            if int(entry.get("destination", "0xFF"), 16) != 0:
                continue
            if int(entry.get("mask", "0xFF"), 16) != 0:
                continue
            flags = entry.get("flags", "0x00")
            if is_up(flags) and is_gateway(flags):
                iface = entry.get("iface", None)
                break

    return iface


def get_ifaddresses_by_default_route() -> dict:
    """Get address configuration from interface associated with default gateway."""
    interface = "lo"
    ip = "127.0.0.1"
    netmask = "255.0.0.0"

    # TOCHK: Gathering only IPv4
    default_gateways = netifaces.gateways().get("default", {})
    if default_gateways and netifaces.AF_INET in default_gateways:
        interface = netifaces.gateways()["default"][netifaces.AF_INET][1]
    else:
        # There are some cases where netifaces doesn't return the machine's default
        # gateway, but it does exist. Let's check the /proc/net/route table to see
        # if we can find the proper gateway.
        interface = _get_default_gw_iface_fallback() or "lo"

    ip_list = netifaces.ifaddresses(interface)[netifaces.AF_INET]
    if len(ip_list) > 0 and "addr" in ip_list[0]:
        return ip_list[0]

    return {"addr": ip, "netmask": netmask}


def get_local_ip_by_default_route() -> str:
    """Get IP address of host associated with default gateway."""
    return get_ifaddresses_by_default_route()["addr"]


def get_local_cidr_from_ip_address(
    ip_address: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str:
    """Find the local CIDR matching the IP address.

    If the IP address is not a local ip address, it should still
    try to return a matching cidr.
    """
    if isinstance(ip_address, str):
        ip_address = ipaddress.ip_address(ip_address)
    with NDB() as ndb:
        for parsed_address in ndb.addresses.values():
            network = ipaddress.ip_network(
                parsed_address["address"] + "/" + str(parsed_address["prefixlen"]),
                strict=False,
            )
            if ip_address in network:
                return str(network)
    raise ValueError(f"No local CIDR found for IP address {ip_address}")


def get_local_ip_by_cidr(cidr: str) -> str:
    """Get first IP address of host associated with CIDR."""
    network = ipaddress.ip_network(cidr, strict=True)
    with NDB() as ndb:
        for parsed_address in ndb.addresses.values():
            address = ipaddress.ip_address(parsed_address["address"])
            if address in network:
                return str(address)
    raise ValueError(f"No local IP address found for CIDR {cidr}")


def get_local_ifname_by_cidr(cidr: str) -> str:
    """Get ifname of the first IP address of host associated with CIDR."""
    network = ipaddress.ip_network(cidr, strict=True)
    with NDB() as ndb:
        for parsed_address in ndb.addresses.summary():
            address = ipaddress.ip_address(parsed_address["address"])
            if address in network:
                return parsed_address["ifname"]
    raise ValueError(f"No local interface found for CIDR {cidr}")


def get_local_cidr_by_default_route() -> str:
    """Get CIDR of host associated with default gateway."""
    conf = get_ifaddresses_by_default_route()
    ip = conf["addr"]
    netmask = conf["netmask"]
    network = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
    return str(network)


def get_nameservers(ipv4_only=True, max_count=5) -> list[str]:
    """Return a list of nameservers used by the host."""
    resolve_config = Path("/run/systemd/resolve/resolv.conf")
    nameservers = []
    try:
        with open(resolve_config, "r") as f:
            contents = f.readlines()
        nameservers = [
            line.split()[1] for line in contents if re.match(r"^\s*nameserver\s", line)
        ]
        if ipv4_only:
            nameservers = [n for n in nameservers if not re.search("[a-zA-Z]", n)]
        # De-duplicate the list of nameservers
        nameservers = list(set(nameservers))
    except FileNotFoundError:
        nameservers = []
    return nameservers[:max_count]


class CatchGroup(click.Group):
    """Catch exceptions and print them to stderr."""

    def __call__(self, *args, **kwargs):
        """Don't display traceback on exceptions."""
        try:
            return self.main(*args, **kwargs)
        except SunbeamException as e:
            LOG.debug(e, exc_info=True)
            LOG.error("Error: %s", e)
            sys.exit(1)
        except Exception as e:
            LOG.debug(e, exc_info=True)
            message = (
                "An unexpected error has occurred."
                " Please see https://canonical-openstack.readthedocs-hosted.com/en/latest/how-to/troubleshooting/inspecting-the-cluster/"
                " for troubleshooting information."
            )
            LOG.warn(message)
            LOG.error("Error: %s", e)
            sys.exit(1)


K = typing.TypeVar("K")
V = typing.TypeVar("V")

_MERGE_DICT = dict[K, V]


def merge_dict(d: _MERGE_DICT, u: _MERGE_DICT) -> _MERGE_DICT:
    """Merges nested dicts and updates the first dict."""
    for k, v in u.items():
        if not d.get(k):
            d[k] = v
        elif issubclass(type(v), collections.abc.Mapping):
            d[k] = merge_dict(d.get(k, {}), v)
        else:
            if v:
                d[k] = v
    return d


def get_local_cidr_matching_token(token: str) -> str:
    """Look up a local ip matching addresses in the join token."""
    token_dict = json.loads(base64.b64decode(token))
    join_addresses = token_dict["join_addresses"]
    LOG.debug("Join addresses: %s", join_addresses)
    for join_address in join_addresses:
        try:
            ip_address = join_address.rsplit(":", 1)[0]
            return get_local_cidr_from_ip_address(ip_address)
        except ValueError:
            pass
    raise ValueError("No local networks found matching join token addresses.")


def random_string(length: int) -> str:
    """Utility function to generate secure random string."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(length))


def generate_password() -> str:
    """Generate a password."""
    return random_string(12)


def first_connected_server(servers: list) -> str | None:
    """Return first connected server from this node.

    servers is expected to be of format ["<ip>:<port>", ...]
    """
    for server in servers:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ip_port = server.rsplit(":", 1)
        if len(ip_port) != 2:
            LOG.debug(f"Server {server} not in <ip>:<port> format")
            continue

        ip = ipaddress.ip_address(ip_port[0].lstrip("[").rstrip("]"))
        port = int(ip_port[1])

        try:
            if isinstance(ip, ipaddress.IPv4Address):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            else:
                s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)

            s.settimeout(30)  # 30 seconds timeout
            s.connect((str(ip), port))
            return server
        except Exception as e:
            LOG.debug(str(e))
            LOG.debug(f"Not able to connect to {ip} {port}")
        finally:
            s.close()

    return None


def click_option_show_hints(func: click.decorators.FC) -> click.decorators.FC:
    """Common decorator to show hints for questions."""
    return click.option(
        "--hints/--no-hints",
        "show_hints",
        is_flag=True,
        default=True,
        help="Display question hints.",
    )(func)


def pass_method_obj(f):
    """Pass the context object to a method.

    Similar to :func:`pass_context`, but only pass the object on the
    context onwards (:attr:`Context.obj`).  This is useful if that object
    represents the state of a nested system.
    """
    import click

    def new_func(self, *args, **kwargs):
        return f(self, click.get_current_context().obj, *args, **kwargs)

    return update_wrapper(new_func, f)


class DefaultableMappingParameter(click.ParamType):
    name = "defaultable_mapping"

    def __init__(self, key: str, value: str, separator: str = ":"):
        self.key_name = key
        self.value_name = value
        self.sep = separator

    def to_info_dict(self) -> dict:
        """Return a dictionary containing the parameter's information."""
        info_dict = super().to_info_dict()
        info_dict["key_name"] = self.key_name
        info_dict["value_name"] = self.value_name
        info_dict["separator"] = self.sep
        return info_dict

    def convert(self, value: str, param, ctx) -> tuple[str, str]:
        """Convert the value to a tuple.

        First member of the tuple is the key and the second member is the value.
        """
        split = value.split(self.sep)
        if len(split) == 1:
            return (split[0], "default")
        elif len(split) == 2:
            return (split[0], split[1])
        self.fail(f"Invalid mapping '{value}'.", param, ctx)

    def get_metavar(self, param: click.Parameter) -> str:
        """Define metavar representation for the parameter."""
        repr_str = f"{self.key_name}|{self.key_name}{self.sep}{self.value_name}"

        # Use curly braces to indicate a required argument.
        if param.required and param.param_type_name == "argument":
            return f"{{{repr_str}}}"

        # Use square braces to indicate an option or optional argument.
        return f"[{repr_str}]"


def clean_env():
    """Remove unwanted environment variables from running process.

    OpenStack variables defined in the environment can interfere with
    the operation of the snap. When these variables are actually needed,
    the snap set them itself.
    """
    for key in os.environ:
        if key.startswith("OS_"):
            os.environ.pop(key)
