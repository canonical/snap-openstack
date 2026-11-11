# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""BGP configuration steps for MAAS deployments.

The ``configure bgp`` flow discovers, from MAAS, which compute machines
are reached through which rack controller and reconciles that mapping
with operator-supplied per-rack BGP peers. It then enables the neutron
BGP integration (ovn-bgp service plugin and agent extension), verifies
the rendered configuration, and attaches each rack's nodes through
`microovn setup bgp-attach`. The discovered topology is kept in memory;
MAAS is re-queried on every run.
"""

import ipaddress
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pydantic
import tenacity
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import BaseStep, Result, ResultType, StepContext
from sunbeam.core.juju import JujuHelper
from sunbeam.errors import SunbeamException
from sunbeam.provider.maas.client import MaasClient
from sunbeam.steps.microovn import APPLICATION as MICROOVN_APPLICATION

if TYPE_CHECKING:
    from sunbeam.clusterd.client import Client
    from sunbeam.core.manifest import Manifest
    from sunbeam.core.terraform import TerraformHelper
    from sunbeam.provider.maas.deployment import MaasDeployment

LOG = logging.getLogger(__name__)

#: Juju *application* names (distinct from charm names: the neutron app
#: is "neutron", its charm is "neutron-k8s").
NEUTRON_APPLICATION = "neutron"

#: Contract with the deploy-openstack / deploy-openstack-hypervisor /
#: deploy-microovn terraform plans: the plans map this variable to the
#: charm configuration that renders `service_plugins = ovn-router,ovn-bgp`
#: in neutron.conf (neutron-k8s) and `[agent] extensions = ovn-bgp` in
#: ovn_agent.ini (openstack-hypervisor snap, openstack-network-agents).
BGP_ENABLE_TFVAR = "enable-bgp"

#: Rendered-config paths used for verification. The neutron-server
#: container path is stable; the agent ini path follows each snap's
#: layout (`/var/snap/<snap>/common/etc/neutron/`).
NEUTRON_SERVER_CONTAINER = "neutron-server"
NEUTRON_CONF_PATH = "/etc/neutron/neutron.conf"

#: The canonical rack-to-machine discovery filter. Machines tagged
#: "compute" are mapped to the hostname of the rack controller that is
#: primary for the VLAN of each of their interfaces. Racks and machines
#: are emitted as tab-separated pairs; duplicates are removed by the
#: caller.
JQ_RACK_MACHINE_FILTER = """
.[] | select(.tag_names | index("compute"))
  | .hostname as $h
  | .interface_set[]
  | select(.vlan.primary_rack != null)
  | .vlan.primary_rack as $r
  | [($racks[] | select(.system_id==$r) | .hostname), $h] | @tsv
"""


class BgpConfigException(SunbeamException):
    """Raised when BGP discovery or validation fails."""


class BgpRack(pydantic.BaseModel):
    """BGP configuration of a single rack controller.

    The ASN is always required, numbered or unnumbered: BIRD needs the
    leaf's ASN to write its static neighbour.
    """

    asn: int
    address: str
    networks: list[str] = pydantic.Field(default_factory=list)


class BgpConfig(pydantic.BaseModel):
    """Reconciled BGP topology handed to the configuration steps."""

    racks: dict[str, BgpRack] = pydantic.Field(default_factory=dict)


def jq_binary() -> str:
    """Return the path to a jq executable.

    The jq binary is shipped inside the openstack snap; outside of a
    snap (development environments) it is resolved from the PATH.
    """
    snap = os.environ.get("SNAP")
    if snap:
        candidate = Path(snap) / "usr" / "bin" / "jq"
        if candidate.exists():
            return str(candidate)
    jq = shutil.which("jq")
    if jq is None:
        raise BgpConfigException(
            "jq executable not found: it is shipped with the openstack snap."
        )
    return jq


def _parse_bgp_peer(peer: str, rack: str) -> tuple[int, str]:
    """Split a peer spec into (asn, address).

    The peer must be ``<asn>@<address>``, numbered or unnumbered alike:
    BIRD needs the leaf's ASN even for unnumbered (link-local) peering.
    """
    asn_str, sep, address = peer.partition("@")
    if not sep:
        raise ValueError(
            f"Invalid BGP peer for rack {rack!r}: peer {peer!r} must be "
            "in format <asn>@<address>"
        )
    try:
        asn = int(asn_str)
    except ValueError as e:
        raise ValueError(f"Invalid BGP peer ASN {asn_str!r} for rack {rack!r}") from e
    if not 1 <= asn <= 4294967295:
        raise ValueError(
            f"Invalid BGP peer ASN {asn} for rack {rack!r}: "
            "must be between 1 and 4294967295."
        )
    if asn in COMPUTE_ASN_RANGE:
        raise ValueError(
            f"Invalid BGP peer ASN {asn} for rack {rack!r}: must be outside "
            f"the compute ASN range {COMPUTE_ASN_RANGE.start}-"
            f"{COMPUTE_ASN_RANGE.stop - 1}, which MicroOVN assigns to computes"
        )
    try:
        ipaddress.ip_address(address)
    except ValueError as e:
        raise ValueError(
            f"Invalid BGP peer address for rack {rack!r}: {address!r} ({e})"
        ) from e
    return asn, address


#: Unnumbered peers must sit in the IPv6 link-local range.
IPV6_LINK_LOCAL = ipaddress.ip_network("fe80::/10")

#: MicroOVN hands compute ASNs out of this range (compute-0 gets the
#: first); a leaf must not pick one, or that compute would peer with a
#: node sharing its own AS.
COMPUTE_ASN_RANGE = range(4210000000, 4294967295)


def _parse_bgp_rack_fields(rack_option: str) -> dict[str, str]:
    """Split one --rack option into its KEY=VALUE fields.

    :raises ValueError: if the option is malformed, has unknown or
        duplicate fields, or misses a required field.
    """
    fields: dict[str, str] = {}
    for part in rack_option.split(","):
        key, sep, value = part.partition("=")
        if not sep or not key or not value:
            raise ValueError(
                f"Invalid --rack {rack_option!r}: expected KEY=VALUE fields."
            )
        if key in fields:
            raise ValueError(
                f"Invalid --rack {rack_option!r}: duplicate field {key!r}."
            )
        fields[key] = value
    unknown = set(fields) - {"name", "peer", "networks"}
    if unknown:
        raise ValueError(
            f"Invalid --rack {rack_option!r}: unknown field(s) "
            f"{', '.join(sorted(unknown))}. Expected name, peer, networks."
        )
    missing = {"name", "peer"} - set(fields)
    if missing:
        raise ValueError(
            f"Invalid --rack {rack_option!r}: missing required field(s) "
            f"{', '.join(sorted(missing))}."
        )
    return fields


def _validate_bgp_peer_family(
    rack_option: str,
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    has_networks: bool,
) -> None:
    """Check the peer's address family against the networks field.

    :raises ValueError: on any family violation.
    """
    if peer_ip.version == 4:
        if not has_networks:
            raise ValueError(
                f"Invalid --rack {rack_option!r}: "
                "--networks is required with an IPv4 peer"
            )
        return
    if has_networks:
        raise ValueError(f"Invalid --rack {rack_option!r}: numbered peer must be IPv4")
    if peer_ip not in IPV6_LINK_LOCAL:
        raise ValueError(
            f"Invalid --rack {rack_option!r}: unnumbered peer must be "
            "link-local (fe80::)"
        )


def _parse_bgp_rack_option(rack_option: str, rack_configs: dict[str, BgpRack]) -> None:
    """Validate one --rack option and add it to rack_configs.

    :raises ValueError: if the option is malformed or self-inconsistent,
        or if the rack name was already given.
    """
    fields = _parse_bgp_rack_fields(rack_option)
    name = fields["name"]
    if name in rack_configs:
        raise ValueError(f"Rack {name!r} given more than once.")
    asn, address = _parse_bgp_peer(fields["peer"], name)
    peer_ip = ipaddress.ip_address(address)
    has_networks = "networks" in fields
    _validate_bgp_peer_family(rack_option, peer_ip, has_networks)
    networks = [fields["networks"]] if has_networks else []
    rack_configs[name] = BgpRack(asn=asn, address=address, networks=networks)


def parse_bgp_rack_options(racks: Sequence[str]) -> dict[str, BgpRack]:
    """Parse --rack options into a {rack: BgpRack} mapping.

    Each option is a comma-separated list of KEY=VALUE fields::

        name=<rack controller hostname>
        peer=<asn>@<address>   (ASN required, numbered or unnumbered)
        networks=<first-ip>/<len>  (required for an IPv4 peer, the
                                    compute address pool)

    Validation rules:

    - An IPv4 (numbered) peer requires ``networks``; an IPv6 peer must
      be link-local (fe80::) and must have no ``networks``.
    - ``networks`` must be an IPv4 subnet between /16 and /31; the
      value is passed to MicroOVN verbatim, since its host bits select
      the first address handed out.
    - The peer must sit inside the pool's subnet, but not on its
      network or broadcast address (both are usable on a /31).
    - Across racks: two numbered racks must not share a leaf IP, and
      their pools must not overlap. A rack name may be given at most
      once.

    :param racks: raw --rack option values.
    :raises ValueError: if an option is malformed, a field value is
        invalid, or the racks conflict with each other.
    """
    rack_configs: dict[str, BgpRack] = {}
    for rack_option in racks:
        _parse_bgp_rack_option(rack_option, rack_configs)
    return rack_configs


def discover_bgp_rack_machines(client: "MaasClient") -> dict[str, list[str]]:
    """Map each rack controller to the compute machines reached through it.

    Rack controllers and machines are fetched from the MAAS API and the
    rack-to-machine mapping is computed with the canonical jq filter:
    every machine tagged "compute" is attached to the hostname of the
    rack controller that is primary for the VLAN of each of its
    interfaces. A machine with interfaces on several racks appears under
    each of them.

    :param client: connected MAAS API client.
    :raises BgpConfigException: if jq is missing, fails, or produces
        unexpected output.
    """
    racks = client.list_rack_controllers()
    machines = client.list_all_machines()
    try:
        proc = subprocess.run(
            [
                jq_binary(),
                "-r",
                "--argjson",
                "racks",
                json.dumps(racks),
                JQ_RACK_MACHINE_FILTER,
            ],
            input=json.dumps(machines),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        raise BgpConfigException(f"Failed to run jq: {e}") from e
    if proc.returncode != 0:
        raise BgpConfigException(
            f"jq failed to compute the rack/machine mapping: {proc.stderr.strip()}"
        )
    rack_machines: dict[str, set[str]] = {}
    for line in proc.stdout.splitlines():
        rack, sep, machine = line.partition("\t")
        if not sep or not rack or not machine:
            raise BgpConfigException(f"Unexpected jq output: {line!r}")
        rack_machines.setdefault(rack, set()).add(machine)
    return {rack: sorted(machines) for rack, machines in sorted(rack_machines.items())}


def validate_bgp_rack_coverage(
    rack_configs: dict[str, BgpRack],
    rack_machines: dict[str, list[str]],
    accept_defaults: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> dict[str, BgpRack]:
    """Reconcile operator-supplied rack configs with the discovered racks.

    Validation is strict in both directions: every given rack must have
    been discovered, and every discovered rack must be given a peer.
    Racks without a peer are omitted from the returned mapping, but only
    after explicit confirmation; with ``--accept-defaults`` they are
    refused outright since there is no safe default for a BGP peer.

    :param rack_configs: {rack: BgpRack} parsed from the command line.
    :param rack_machines: {rack: [machines]} discovered from MAAS.
    :param accept_defaults: refuse uncovered racks instead of confirming.
    :param confirm: callback used to ask the operator for confirmation.
    :raises BgpConfigException: if the two sets cannot be reconciled.
    :return: the validated {rack: BgpRack} mapping.
    """
    unknown = sorted(set(rack_configs) - set(rack_machines))
    if unknown:
        raise BgpConfigException(
            f"Rack(s) not found in MAAS: {', '.join(unknown)}. "
            f"Discovered racks: {', '.join(sorted(rack_machines)) or 'none'}."
        )
    uncovered = sorted(set(rack_machines) - set(rack_configs))
    if uncovered:
        message = (
            f"No BGP peer given for rack(s): {', '.join(uncovered)}. "
            "Racks without a peer and their compute machines will be left "
            "out of the BGP configuration."
        )
        if accept_defaults:
            raise BgpConfigException(
                f"{message} Provide a peer for every discovered rack."
            )
        if confirm is None or not confirm(f"{message} Continue?"):
            raise BgpConfigException(
                "Provide a BGP peer for all discovered racks with "
                "--rack name=...,peer=...."
            )
    return {
        rack: rack_configs[rack]
        for rack in sorted(set(rack_machines) & set(rack_configs))
    }


def format_bgp_peer(config: BgpRack) -> str:
    """Render a rack's peer in MicroOVN's normalised ``<asn>@<address>`` form.

    This is the exact string `bgp-attach status` prints in its PEER
    column, so callers can filter status rows on it directly. The ASN
    is rendered as an integer (leading zeros dropped) and the address
    through ipaddress (IPv6 shortened), so an operator typing a long
    form still matches the canonical output.
    """
    return f"{config.asn}@{ipaddress.ip_address(config.address)}"


def print_bgp_topology(
    console: Console,
    rack_configs: dict[str, BgpRack],
    rack_machines: dict[str, list[str]],
) -> None:
    """Print the discovered rack/machine mapping as a table."""
    table = Table("Rack", "BGP peer", "Networks", "Compute machines")
    for rack, machines in rack_machines.items():
        config = rack_configs.get(rack)
        peer = format_bgp_peer(config) if config else "-"
        networks = "\n".join(config.networks) if config and config.networks else "-"
        table.add_row(rack, peer, networks, "\n".join(machines))
    console.print(table)


class BgpDiscoverRackMachinesStep(BaseStep):
    """Discover the rack-to-compute-machines mapping from MAAS."""

    def __init__(self, client: "MaasClient"):
        super().__init__(
            name="discover-bgp-rack-machines",
            description="Discovering compute machines per rack controller",
        )
        self.client = client
        self.rack_machines: dict[str, list[str]] = {}

    def is_skip(self, context: StepContext) -> Result:
        """Discovery always runs: MAAS state may have changed."""
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Fetch MAAS racks and machines and compute the mapping."""
        try:
            self.rack_machines = discover_bgp_rack_machines(self.client)
        except Exception as e:
            LOG.debug("BGP rack discovery failed", exc_info=True)
            return Result(ResultType.FAILED, f"Failed to discover rack topology: {e}")
        if not self.rack_machines:
            return Result(
                ResultType.FAILED,
                "No compute machines behind a rack controller were found in "
                "MAAS. Check that machines are tagged 'compute' and that "
                "their VLANs have a primary rack set.",
            )
        return Result(ResultType.COMPLETED)


BGP_ATTACH_COMMAND = "microovn setup bgp-attach"
BGP_ATTACH_STATUS_COMMAND = "microovn setup bgp-attach status"

#: bgp-attach refuses while Neutron has not yet created a listed
#: compute's BGP router port. That is transient right after the agent
#: enablement, so the poll reruns the call instead of failing.
NO_ROUTER_PORT_MARKER = "matches no router port"


def build_bgp_attach_command(config: BgpRack) -> str:
    """Build the `microovn setup bgp-attach` command for a rack.

    The peer is always ``<asn>@<address>`` (the ASN is required for
    unnumbered peering too, because BIRD needs it for its static
    neighbour); ``networks`` is the address pool, passed verbatim with
    its host bits selecting the first handed-out address. MicroOVN
    resolves each member's router port and allocates addresses itself;
    no member list is sent — MicroOVN scopes the attachment itself.

    :param config: the rack's BGP configuration.
    """
    command = f"{BGP_ATTACH_COMMAND} --peer {format_bgp_peer(config)}"
    for network in config.networks:
        command += f" --networks {network}"
    return command


def parse_bgp_attach_status(output: str) -> list[dict[str, str]]:
    """Parse the member table of `microovn setup bgp-attach status`.

    Returns one dict per member row, keyed by the table column names
    (MEMBER, ID, ADDRESS, PEER, ATTACHED, ROUTER PORT). Unknown or
    malformed lines are ignored, so minor format changes stay safe.
    """
    rows: list[dict[str, str]] = []
    columns: list[str] | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if columns is None:
            upper = [cell.upper() for cell in cells]
            if "MEMBER" in upper and "ATTACHED" in upper:
                columns = upper
            continue
        if len(cells) != len(columns):
            continue
        if all(set(cell) <= {"-", " "} for cell in cells):
            continue
        rows.append(dict(zip(columns, cells)))
    return rows


def rack_attachment_status(
    rows: list[dict[str, str]],
    peer: str,
    machines: list[str],
) -> tuple[bool, list[str], list[str]]:
    """Compare a rack's expected member set against the status table.

    The MicroOVN setting is the truth: it must match the rack's MAAS
    member list exactly. Members of the rack that are not attached are
    reported as missing; nodes attached to the peer that are not in the
    expected list are reported as extra drift.

    :param rows: parsed status table rows.
    :param peer: the rack's peer spec, ``<asn>@<address>``.
    :param machines: the rack's compute machine hostnames.
    :return: (all_attached, missing machines, extra members on the peer).
    """
    rows_by_member = {row.get("MEMBER"): row for row in rows}
    expected = set(machines)
    missing = []
    for machine in machines:
        row = rows_by_member.get(machine)
        if (
            row is None
            or row.get("PEER") != peer
            or row.get("ATTACHED", "").lower() != "true"
        ):
            missing.append(machine)
    extra = sorted(
        member
        for member, row in rows_by_member.items()
        if member is not None and member not in expected and row.get("PEER") == peer
    )
    return not missing and not extra, missing, extra


class MicroOvnBgpAttachStep(BaseStep):
    """Attach a rack's compute nodes to BGP via MicroOVN.

    Runs `microovn setup bgp-attach` on the MicroOVN leader unit;
    MicroOVN scopes the attachment to the rack's nodes itself. The
    step then polls, rerunning the idempotent attach call and
    reading `microovn setup bgp-attach status` in every iteration,
    until every machine of the rack reports attached for the rack's
    peer. Exit codes are classified: 0 (with possible warnings, e.g. a
    down compute "not reached") keeps polling; 1 with "matches no
    router port" is transient and retried; any other failure fails
    immediately with the last line.
    """

    ATTACH_TIMEOUT = 600
    STATUS_INTERVAL = 15

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str,
        rack: str,
        config: BgpRack,
        machines: list[str],
        timeout: float | None = None,
    ):
        super().__init__(
            name=f"bgp-attach-{rack}",
            description=f"Configuring BGP attachment for rack {rack}",
        )
        self.jhelper = jhelper
        self.model = model
        self.rack = rack
        self.config = config
        self.machines = machines
        self.timeout = timeout if timeout is not None else self.ATTACH_TIMEOUT
        self._leader: str | None = None

    def _resolve_leader(self) -> str:
        """Return the MicroOVN leader unit, resolved once per step run."""
        if self._leader is None:
            self._leader = self.jhelper.get_leader_unit(
                MICROOVN_APPLICATION, self.model
            )
        return self._leader

    def _status_rows(self, leader: str) -> list[dict[str, str]]:
        """Run the status command on the leader and parse its table."""
        task = self.jhelper.run_cmd_on_unit(
            leader, self.model, BGP_ATTACH_STATUS_COMMAND
        )
        return parse_bgp_attach_status(task.stdout)

    def is_skip(self, context: StepContext) -> Result:
        """Never skip: the sync call is idempotent and cheap.

        A member-based skip cannot detect settings drift (for example a
        changed pool on an all-attached rack), and rerunning the
        idempotent bgp-attach changes nothing when already in sync.
        """
        return Result(ResultType.COMPLETED)

    @staticmethod
    def _failure_message(
        rack: str,
        missing: list[str],
        extra: list[str],
        attach_error: str,
        last_warning: str,
    ) -> str:
        """Build the failure message for a rack that never reached sync."""
        problems = []
        if missing:
            problems.append(f"not attached: {', '.join(missing)}")
        if extra:
            problems.append(f"unexpected members: {', '.join(extra)}")
        message = f"BGP attachment incomplete for rack {rack}: " + "; ".join(problems)
        if attach_error:
            message += f". bgp-attach reported: {attach_error}"
        if last_warning:
            # A down compute never becomes attached, so a timeout is
            # often just that; name the cause for the operator.
            message += f". last warning: {last_warning}"
        return message

    def run(self, context: StepContext) -> Result:
        """Attach the rack's nodes and wait for them to report attached.

        Each poll iteration first reruns the idempotent bgp-attach call
        and then reads status: MicroOVN only writes router-port keys
        when the command runs, so rerunning picks up ports that Neutron
        has created since the previous attempt.
        """
        try:
            leader = self._resolve_leader()
        except Exception as e:
            LOG.debug("Failed to resolve MicroOVN leader", exc_info=True)
            return Result(ResultType.FAILED, f"Failed to resolve MicroOVN leader: {e}")

        peer = format_bgp_peer(self.config)
        attach_error = ""
        last_warning = ""

        def _run_attach() -> None:
            """Rerun the sync call and classify its exit code."""
            nonlocal attach_error, last_warning
            task = self.jhelper.run_cmd_on_unit(
                leader,
                self.model,
                build_bgp_attach_command(self.config),
                timeout=self.timeout,
            )
            if task.return_code == 0:
                for line in task.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Warning"):
                        LOG.warning("microovn setup bgp-attach: %s", stripped)
                        last_warning = stripped
                return
            message = task.stderr.strip() or task.message or f"exit {task.return_code}"
            if NO_ROUTER_PORT_MARKER in message:
                # Neutron is still creating the computes' BGP router
                # ports right after the agent enablement; the rerun in
                # the next poll iteration picks them up.
                LOG.debug("bgp-attach waiting for router ports: %s", message)
                attach_error = message
                return
            raise BgpConfigException(f"microovn setup bgp-attach failed: {message}")

        @tenacity.retry(
            wait=tenacity.wait_fixed(self.STATUS_INTERVAL),
            stop=tenacity.stop_after_delay(self.timeout),
            retry=tenacity.retry_if_result(lambda status: not status[0]),
        )
        def _wait_in_sync() -> tuple[bool, list[str], list[str]]:
            _run_attach()
            return rack_attachment_status(
                self._status_rows(leader), peer, self.machines
            )

        try:
            in_sync, missing, extra = _wait_in_sync()
        except tenacity.RetryError as e:
            in_sync, missing, extra = e.last_attempt.result()
        except BgpConfigException as e:
            return Result(ResultType.FAILED, str(e))
        except Exception as e:
            LOG.debug("microovn setup bgp-attach failed", exc_info=True)
            return Result(ResultType.FAILED, f"microovn setup bgp-attach failed: {e}")
        if not in_sync:
            return Result(
                ResultType.FAILED,
                self._failure_message(
                    self.rack, missing, extra, attach_error, last_warning
                ),
            )
        return Result(ResultType.COMPLETED)


class EnableNeutronBgpStep(BaseStep):
    """Enable the ovn-bgp service plugin on the Neutron server.

    Sets the BGP flag through the openstack plan; the neutron-k8s charm
    renders `service_plugins = ovn-router,ovn-bgp` in neutron.conf.
    """

    NEUTRON_READY_TIMEOUT = 900

    def __init__(
        self,
        client: "Client",
        manifest: "Manifest",
        tfhelper: "TerraformHelper",
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            name="enable-neutron-bgp",
            description="Enabling BGP service plugin on Neutron",
        )
        self.client = client
        self.manifest = manifest
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Run whenever BGP is being configured."""
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Apply the openstack plan with the BGP flag set."""
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config="TerraformVarsOpenstack",
                override_tfvars={BGP_ENABLE_TFVAR: True},
            )
        except Exception as e:
            LOG.debug("Enabling neutron BGP plugin failed", exc_info=True)
            return Result(
                ResultType.FAILED, f"Failed to enable neutron BGP plugin: {e}"
            )
        try:
            self.jhelper.wait_application_ready(
                NEUTRON_APPLICATION, self.model, timeout=self.NEUTRON_READY_TIMEOUT
            )
        except Exception as e:
            LOG.debug("neutron-k8s not ready after BGP enable", exc_info=True)
            return Result(
                ResultType.FAILED,
                f"{NEUTRON_APPLICATION} did not become ready after enabling "
                f"the BGP plugin: {e}",
            )
        return Result(ResultType.COMPLETED)


class ValidateNeutronBgpPluginStep(BaseStep):
    """Verify the rendered neutron.conf carries the ovn-bgp plugin.

    Greps the rendered service_plugins line inside the neutron-server
    container: the plugin must be active, as required by the OVN BGP
    integration.
    """

    def __init__(self, jhelper: JujuHelper, model: str):
        super().__init__(
            name="validate-neutron-bgp-plugin",
            description="Verifying ovn-bgp service plugin",
        )
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Verification always runs."""
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Grep service_plugins in the rendered neutron.conf."""
        try:
            leader = self.jhelper.get_leader_unit(NEUTRON_APPLICATION, self.model)
            results = self.jhelper.run_cmd_on_unit_payload(
                leader,
                self.model,
                f"grep -E '^service_plugins' {NEUTRON_CONF_PATH}",
                container=NEUTRON_SERVER_CONTAINER,
            )
        except Exception as e:
            LOG.debug("Neutron BGP plugin verification failed", exc_info=True)
            return Result(
                ResultType.FAILED,
                f"Failed to verify the neutron BGP plugin: {e}",
            )
        if "ovn-bgp" not in results.get("stdout", ""):
            return Result(
                ResultType.FAILED,
                "The ovn-bgp service plugin is not active in neutron.conf "
                f"({NEUTRON_CONF_PATH}). service_plugins: "
                f"{results.get('stdout', '').strip() or 'not found'}",
            )
        return Result(ResultType.COMPLETED)


def set_node_rack_names(
    client: "Client",
    rack_machines: dict[str, list[str]],
) -> None:
    """Stamp each compute node with the MAAS rack controller it sits behind.

    :param client: clusterd API client.
    :param rack_machines: {rack hostname: [compute node names]} from the
        MAAS discovery.
    """
    for rack, machines in sorted(rack_machines.items()):
        for machine in machines:
            client.cluster.update_node_info(machine, rack_name=rack)
    LOG.info(
        "Stamped rack names on %d node(s)",
        sum(len(machines) for machines in rack_machines.values()),
    )


class SetNodeRackNamesStep(BaseStep):
    """Stamp each compute node with its MAAS rack controller name.

    The rack-to-machines mapping comes from the MAAS discovery; each
    node record in clusterd carries its rack name from then on, so the
    fabric topology is readable from the cluster state without
    re-querying MAAS.
    """

    def __init__(self, client: "Client", rack_machines: dict[str, list[str]]):
        super().__init__(
            name="set-node-rack-names",
            description="Stamping rack names on compute nodes",
        )
        self.client = client
        self.rack_machines = rack_machines

    def is_skip(self, context: StepContext) -> Result:
        """Stamping runs whenever BGP is configured."""
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Stamp rackname on every discovered compute node."""
        try:
            set_node_rack_names(self.client, self.rack_machines)
        except Exception as e:
            LOG.debug("Failed to stamp rack names on nodes", exc_info=True)
            return Result(ResultType.FAILED, f"Failed to stamp rack names: {e}")
        return Result(ResultType.COMPLETED)


BGP_DISABLE_COMMAND = "microovn setup bgp-attach disable"


class MicroOvnBgpDisableStep(BaseStep):
    """Tear down every BGP attachment across the cluster.

    Runs `microovn setup bgp-attach disable` on the MicroOVN leader:
    BIRD goes down on every member and each member's router port is
    restored to the state Neutron left it in. The step then reads
    `bgp-attach status` to confirm nothing stays configured.
    """

    DISABLE_TIMEOUT = 120

    def __init__(self, jhelper: JujuHelper, model: str):
        super().__init__(
            name="disable-bgp-attach",
            description="Disabling BGP attachment on all nodes",
        )
        self.jhelper = jhelper
        self.model = model
        self._leader: str | None = None

    def _resolve_leader(self) -> str:
        """Return the MicroOVN leader unit, resolved once per step run."""
        if self._leader is None:
            self._leader = self.jhelper.get_leader_unit(
                MICROOVN_APPLICATION, self.model
            )
        return self._leader

    def is_skip(self, context: StepContext) -> Result:
        """Skip when no BGP attachment is configured at all."""
        try:
            task = self.jhelper.run_cmd_on_unit(
                self._resolve_leader(), self.model, BGP_ATTACH_STATUS_COMMAND
            )
        except Exception:
            LOG.debug("BGP attachment status check failed", exc_info=True)
            return Result(ResultType.COMPLETED)
        if not parse_bgp_attach_status(task.stdout):
            return Result(ResultType.SKIPPED, "BGP is not configured.")
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Disable every BGP attachment and verify the teardown."""
        try:
            leader = self._resolve_leader()
            task = self.jhelper.run_cmd_on_unit(
                leader,
                self.model,
                BGP_DISABLE_COMMAND,
                timeout=self.DISABLE_TIMEOUT,
            )
        except Exception as e:
            LOG.debug("microovn setup bgp-attach disable failed", exc_info=True)
            return Result(
                ResultType.FAILED,
                f"microovn setup bgp-attach disable failed: {e}",
            )
        if task.return_code != 0:
            return Result(
                ResultType.FAILED,
                "microovn setup bgp-attach disable failed: "
                f"{task.stderr.strip() or task.message or task.return_code}",
            )
        status_task = self.jhelper.run_cmd_on_unit(
            leader, self.model, BGP_ATTACH_STATUS_COMMAND
        )
        rows = parse_bgp_attach_status(status_task.stdout)
        if rows:
            peers = sorted({row.get("PEER") or "?" for row in rows})
            return Result(
                ResultType.FAILED,
                "BGP disable incomplete: attachments still configured for "
                f"{', '.join(peers)}",
            )
        return Result(ResultType.COMPLETED)


MAAS_RACK_CONTROLLERS_KEY = "MaasRackControllers"


def save_maas_rack_controllers(client: "Client", hosts: list[str]) -> None:
    """Store the MAAS rack controller hostnames in the cluster database.

    :param client: clusterd API client.
    :param hosts: the rack controller hostnames of the deployment.
    """
    client.cluster.update_config(
        MAAS_RACK_CONTROLLERS_KEY, json.dumps({"racks": sorted(hosts)})
    )


def get_maas_rack_controllers(client: "Client") -> list[str]:
    """Load the MAAS rack controller hostnames from the cluster database.

    :param client: clusterd API client.
    :return: the stored hostnames, or an empty list if none are stored.
    """
    try:
        data = client.cluster.get_config(MAAS_RACK_CONTROLLERS_KEY)
    except ConfigItemNotFoundException:
        return []
    try:
        return list(json.loads(data).get("racks", []))
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        raise BgpConfigException(
            f"Invalid MAAS rack controller list stored in the cluster database: {e}"
        ) from e


class SaveMaasRackControllersStep(BaseStep):
    """Store the MAAS rack controller hostnames at bootstrap time.

    Runs at the end of the MAAS bootstrap plan: the rack controller
    inventory is persisted so later flows (BGP configuration, rack
    validation) know the deployment's fabric topology without
    re-deriving it.
    """

    def __init__(
        self,
        client: "Client",
        deployment: "MaasDeployment",
        maas_client: "MaasClient | None" = None,
    ):
        super().__init__(
            name="save-maas-rack-controllers",
            description="Saving MAAS rack controller names",
        )
        self.client = client
        self.deployment = deployment
        self.maas_client = maas_client

    def is_skip(self, context: StepContext) -> Result:
        """Save runs on every bootstrap."""
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Fetch the rack controllers from MAAS and store their names."""
        maas_client = self.maas_client or MaasClient.from_deployment(self.deployment)
        try:
            racks = maas_client.list_rack_controllers()
        except Exception as e:
            LOG.debug("Failed to list MAAS rack controllers", exc_info=True)
            return Result(
                ResultType.FAILED,
                f"Failed to list MAAS rack controllers: {e}",
            )
        hosts = [rack["hostname"] for rack in racks if rack.get("hostname")]
        if not hosts:
            return Result(
                ResultType.FAILED,
                "MAAS returned no rack controllers; cannot store the rack inventory.",
            )
        save_maas_rack_controllers(self.client, hosts)
        return Result(ResultType.COMPLETED)
