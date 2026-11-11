# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import itertools
from unittest.mock import MagicMock

import jubilant
import pytest

from sunbeam.core.common import ResultType
from sunbeam.provider.maas import bgp
from sunbeam.provider.maas.bgp import (
    BGP_ENABLE_TFVAR,
    BgpConfigException,
    BgpDiscoverRackMachinesStep,
    BgpRack,
    EnableNeutronBgpStep,
    MicroOvnBgpAttachStep,
    SaveMaasRackControllersStep,
    SetNodeRackNamesStep,
    ValidateNeutronBgpPluginStep,
    build_bgp_attach_command,
    discover_bgp_rack_machines,
    format_bgp_peer,
    parse_bgp_attach_status,
    parse_bgp_rack_options,
    rack_attachment_status,
    validate_bgp_rack_coverage,
)

RACKS_JSON = [
    {"system_id": "rack1sid", "hostname": "rack1"},
    {"system_id": "rack2sid", "hostname": "rack2"},
]

MACHINES_JSON = [
    {
        "hostname": "node-a",
        "tag_names": ["compute"],
        "interface_set": [
            {"vlan": {"primary_rack": "rack1sid"}},
            {"vlan": {"primary_rack": None}},
        ],
    },
    {
        "hostname": "node-b",
        "tag_names": ["compute"],
        "interface_set": [{"vlan": {"primary_rack": "rack1sid"}}],
    },
    {
        "hostname": "node-c",
        "tag_names": ["compute"],
        "interface_set": [{"vlan": {"primary_rack": "rack2sid"}}],
    },
    {
        "hostname": "ctrl-1",
        "tag_names": ["control"],
        "interface_set": [{"vlan": {"primary_rack": "rack1sid"}}],
    },
    {
        "hostname": "node-x",
        "tag_names": ["compute"],
        "interface_set": [{"vlan": {}}],
    },
]


@pytest.fixture
def maas_client():
    client = MagicMock()
    client.list_rack_controllers.return_value = RACKS_JSON
    client.list_all_machines.return_value = MACHINES_JSON
    return client


class TestParseBgpRackOptions:
    def test_numbered_peer_with_network(self):
        assert parse_bgp_rack_options(
            ["name=rack1,peer=65101@192.0.2.1,networks=192.0.2.3/28"]
        ) == {
            "rack1": BgpRack(asn=65101, address="192.0.2.1", networks=["192.0.2.3/28"])
        }

    def test_network_passed_verbatim(self):
        # The pool's host bits select the first handed-out address, so
        # the value must reach MicroOVN exactly as the operator typed it.
        parsed = parse_bgp_rack_options(
            ["name=rack1,peer=65101@192.0.2.17,networks=192.0.2.18/28"]
        )
        assert parsed["rack1"].networks == ["192.0.2.18/28"]

    def test_unnumbered_peer_requires_asn(self):
        assert parse_bgp_rack_options(["name=rack1,peer=65101@fe80::1"]) == {
            "rack1": BgpRack(asn=65101, address="fe80::1")
        }

    def test_peer_normalised_for_status_filter(self):
        # MicroOVN prints the peer canonically (shortened IPv6, ASN
        # without leading zeros); the filter string must match that.
        parsed = parse_bgp_rack_options(
            ["name=rack1,peer=065101@fe80:0:0:0:0:0:0:0001"]
        )
        assert format_bgp_peer(parsed["rack1"]) == "65101@fe80::1"

    def test_bare_peer_refused(self):
        with pytest.raises(ValueError, match="must be in format <asn>@<address>"):
            parse_bgp_rack_options(["name=rack1,peer=fe80::1"])

    def test_field_order_is_irrelevant(self):
        assert parse_bgp_rack_options(
            ["peer=65101@192.0.2.1,networks=192.0.2.3/28,name=rack1"]
        ) == {
            "rack1": BgpRack(asn=65101, address="192.0.2.1", networks=["192.0.2.3/28"])
        }

    def test_repeated_rack_rejected(self):
        with pytest.raises(ValueError, match="rack1.*given more than once"):
            parse_bgp_rack_options(
                [
                    "name=rack1,peer=65101@192.0.2.1,networks=192.0.2.0/28",
                    "name=rack1,peer=65101@192.0.2.1,networks=192.0.3.0/28",
                ]
            )

    def test_missing_name(self):
        with pytest.raises(ValueError, match="missing required field"):
            parse_bgp_rack_options(["peer=65101@192.0.2.1"])

    def test_missing_peer(self):
        with pytest.raises(ValueError, match="missing required field"):
            parse_bgp_rack_options(["name=rack1"])

    def test_unknown_field(self):
        with pytest.raises(ValueError, match="unknown field"):
            parse_bgp_rack_options(["name=rack1,peer=fe80::1,foo=bar"])

    def test_duplicate_field(self):
        with pytest.raises(ValueError, match="duplicate field"):
            parse_bgp_rack_options(["name=rack1,name=rack2,peer=fe80::1"])

    def test_malformed_field(self):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            parse_bgp_rack_options(["name=rack1,peer"])

    def test_invalid_asn(self):
        with pytest.raises(ValueError, match="Invalid BGP peer ASN"):
            parse_bgp_rack_options(["name=rack1,peer=abc@192.0.2.1"])

    def test_asn_out_of_range(self):
        with pytest.raises(ValueError, match="between 1 and 4294967295"):
            parse_bgp_rack_options(["name=rack1,peer=0@192.0.2.1"])

    def test_asn_in_compute_range_rejected(self):
        with pytest.raises(ValueError, match="compute ASN range"):
            parse_bgp_rack_options(["name=rack1,peer=4210000003@192.0.2.17"])
        with pytest.raises(ValueError, match="compute ASN range"):
            parse_bgp_rack_options(["name=rack1,peer=4210000000@fe80::1"])
        with pytest.raises(ValueError, match="compute ASN range"):
            parse_bgp_rack_options(["name=rack1,peer=4294967294@fe80::1"])

    def test_asn_above_compute_range_allowed(self):
        assert parse_bgp_rack_options(
            ["name=rack1,peer=4294967295@192.0.2.17,networks=192.0.2.18/28"]
        ) == {
            "rack1": BgpRack(
                asn=4294967295, address="192.0.2.17", networks=["192.0.2.18/28"]
            )
        }

    def test_invalid_peer_address(self):
        with pytest.raises(ValueError, match="Invalid BGP peer address"):
            parse_bgp_rack_options(["name=rack1,peer=65101@nope"])

    def test_ipv4_peer_requires_networks(self):
        with pytest.raises(
            ValueError, match="--networks is required with an IPv4 peer"
        ):
            parse_bgp_rack_options(["name=rack1,peer=65101@192.0.2.17"])

    def test_ipv6_peer_with_networks_refused(self):
        with pytest.raises(ValueError, match="numbered peer must be IPv4"):
            parse_bgp_rack_options(
                ["name=rack1,peer=65101@fe80::1,networks=192.0.2.18/28"]
            )

    def test_ipv6_peer_must_be_link_local(self):
        with pytest.raises(ValueError, match="unnumbered peer must be link-local"):
            parse_bgp_rack_options(["name=rack1,peer=65101@2001:db8::1"])

    def test_mapping(self, maas_client):
        assert discover_bgp_rack_machines(maas_client) == {
            "rack1": ["node-a", "node-b"],
            "rack2": ["node-c"],
        }

    def test_no_compute_machines(self, maas_client):
        maas_client.list_all_machines.return_value = [
            {
                "hostname": "ctrl-1",
                "tag_names": ["control"],
                "interface_set": [{"vlan": {"primary_rack": "rack1sid"}}],
            }
        ]
        assert discover_bgp_rack_machines(maas_client) == {}

    def test_jq_failure(self, maas_client, mocker):
        mocker.patch(
            "sunbeam.provider.maas.bgp.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="boom"),
        )
        with pytest.raises(BgpConfigException, match="boom"):
            discover_bgp_rack_machines(maas_client)

    def test_unexpected_output(self, maas_client, mocker):
        mocker.patch(
            "sunbeam.provider.maas.bgp.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="rack1\n", stderr=""),
        )
        with pytest.raises(BgpConfigException, match="Unexpected jq output"):
            discover_bgp_rack_machines(maas_client)


class TestValidateBgpRackCoverage:
    @pytest.fixture
    def rack_configs(self):
        return {
            "rack1": BgpRack(asn=65101, address="192.0.2.1"),
            "rack2": BgpRack(asn=65102, address="192.0.2.9"),
        }

    def test_all_covered(self, rack_configs):
        assert (
            validate_bgp_rack_coverage(
                rack_configs,
                {"rack1": ["node-a"], "rack2": ["node-c"]},
            )
            == rack_configs
        )

    def test_unknown_rack(self, rack_configs):
        with pytest.raises(BgpConfigException, match="rack9.*rack1"):
            validate_bgp_rack_coverage(
                {"rack9": BgpRack(asn=65101, address="192.0.2.1")},
                {"rack1": ["node-a"]},
            )

    def test_uncovered_with_accept_defaults(self, rack_configs):
        with pytest.raises(BgpConfigException, match="rack2"):
            validate_bgp_rack_coverage(
                {"rack1": rack_configs["rack1"]},
                {"rack1": ["node-a"], "rack2": ["node-c"]},
                accept_defaults=True,
            )

    def test_uncovered_confirmed(self, rack_configs):
        assert validate_bgp_rack_coverage(
            {"rack1": rack_configs["rack1"]},
            {"rack1": ["node-a"], "rack2": ["node-c"]},
            confirm=lambda message: True,
        ) == {"rack1": rack_configs["rack1"]}

    def test_uncovered_aborted(self, rack_configs):
        with pytest.raises(BgpConfigException, match="Provide a BGP peer"):
            validate_bgp_rack_coverage(
                {"rack1": rack_configs["rack1"]},
                {"rack1": ["node-a"], "rack2": ["node-c"]},
                confirm=lambda message: False,
            )

    def test_uncovered_without_confirm(self, rack_configs):
        with pytest.raises(BgpConfigException, match="Provide a BGP peer"):
            validate_bgp_rack_coverage(
                {"rack1": rack_configs["rack1"]},
                {"rack1": ["node-a"], "rack2": ["node-c"]},
            )


class TestBgpDiscoverRackMachinesStep:
    def test_run(self, maas_client, step_context, mocker):
        mocker.patch.object(
            bgp, "discover_bgp_rack_machines", return_value={"rack1": ["node-a"]}
        )
        step = BgpDiscoverRackMachinesStep(maas_client)
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.rack_machines == {"rack1": ["node-a"]}

    def test_run_empty_discovery(self, maas_client, step_context, mocker):
        mocker.patch.object(bgp, "discover_bgp_rack_machines", return_value={})
        step = BgpDiscoverRackMachinesStep(maas_client)
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "compute" in result.message

    def test_run_discovery_error(self, maas_client, step_context, mocker):
        mocker.patch.object(
            bgp,
            "discover_bgp_rack_machines",
            side_effect=BgpConfigException("boom"),
        )
        step = BgpDiscoverRackMachinesStep(maas_client)
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "boom" in result.message


STATUS_HEADER = (
    "|      PEER        |     MEMBER      | ID |    ADDRESS    | ATTACHED |"
    "         ROUTER PORT          |"
)
STATUS_SEPARATOR = (
    "|------------------|-----------------|----|---------------|"
    "----------|------------------------------|"
)


def status_row(member, attached):
    return (
        f"| 65101@192.0.2.17 | {member:<15} | 4  | 192.0.2.21/28 |"
        f" {attached:<8} | bgp-lrp-{member}             |"
    )


STATUS_ALL_ATTACHED = "\n".join(
    [
        "peer: 65101@192.0.2.17 networks: 192.0.2.16/28 advertise-table: 42",
        "",
        STATUS_HEADER,
        STATUS_SEPARATOR,
        status_row("compute-0", "true"),
        status_row("compute-1", "true"),
        "",
    ]
)

STATUS_PARTIAL = "\n".join(
    [
        "peer: 65101@192.0.2.17 networks: 192.0.2.16/28 advertise-table: 42",
        "",
        STATUS_HEADER,
        STATUS_SEPARATOR,
        status_row("compute-0", "true"),
        status_row("compute-1", "false"),
        "",
        "Error: compute-1: not attached yet",
        "bgp-attach is configured, with 1 node error(s)",
        "",
    ]
)


def attach_task(return_code=0, stdout="", stderr=""):
    return jubilant.Task(
        id="1",
        status="completed" if return_code == 0 else "failed",
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
    )


class TestParseBgpAttachStatus:
    def test_rows(self):
        rows = parse_bgp_attach_status(STATUS_PARTIAL)
        assert rows == [
            {
                "MEMBER": "compute-0",
                "ID": "4",
                "ADDRESS": "192.0.2.21/28",
                "PEER": "65101@192.0.2.17",
                "ATTACHED": "true",
                "ROUTER PORT": "bgp-lrp-compute-0",
            },
            {
                "MEMBER": "compute-1",
                "ID": "4",
                "ADDRESS": "192.0.2.21/28",
                "PEER": "65101@192.0.2.17",
                "ATTACHED": "false",
                "ROUTER PORT": "bgp-lrp-compute-1",
            },
        ]

    def test_no_table(self):
        assert parse_bgp_attach_status("bgp-attach is not configured") == []


class TestRackAttachmentStatus:
    ROWS = parse_bgp_attach_status(STATUS_ALL_ATTACHED)

    def test_all_attached(self):
        assert rack_attachment_status(
            self.ROWS, "65101@192.0.2.17", ["compute-0", "compute-1"]
        ) == (True, [], [])

    def test_missing_member(self):
        in_sync, missing, extra = rack_attachment_status(
            self.ROWS, "65101@192.0.2.17", ["compute-0", "compute-2"]
        )
        assert not in_sync
        assert missing == ["compute-2"]
        # compute-1 is still attached to the peer but not expected: drift
        assert extra == ["compute-1"]

    def test_extra_member_is_drift(self):
        rows = self.ROWS + parse_bgp_attach_status(
            "\n".join(
                [STATUS_HEADER, STATUS_SEPARATOR, status_row("compute-9", "true")]
            )
        )
        in_sync, missing, extra = rack_attachment_status(
            rows, "65101@192.0.2.17", ["compute-0", "compute-1"]
        )
        assert not in_sync
        assert missing == []
        assert extra == ["compute-9"]

    def test_other_peers_ignored_for_extra(self):
        other_peer = (
            "| 65102@192.0.2.9 | compute-9        | 5  | 192.0.2.33/28 | true     |"
            " bgp-lrp-compute-9           |"
        )
        rows = self.ROWS + parse_bgp_attach_status(
            "\n".join([STATUS_HEADER, STATUS_SEPARATOR, other_peer])
        )
        assert rack_attachment_status(
            rows, "65101@192.0.2.17", ["compute-0", "compute-1"]
        ) == (True, [], [])

    def test_wrong_peer(self):
        assert rack_attachment_status(self.ROWS, "65102@192.0.2.9", ["compute-0"]) == (
            False,
            ["compute-0"],
            [],
        )

    def test_not_attached(self):
        rows = parse_bgp_attach_status(STATUS_PARTIAL)
        assert rack_attachment_status(
            rows, "65101@192.0.2.17", ["compute-0", "compute-1"]
        ) == (False, ["compute-1"], [])


class TestBuildBgpAttachCommand:
    def test_numbered_with_networks(self):
        command = build_bgp_attach_command(
            BgpRack(asn=65101, address="192.0.2.1", networks=["192.0.2.0/28"])
        )
        assert command == (
            "microovn setup bgp-attach --peer 65101@192.0.2.1 --networks 192.0.2.0/28"
        )

    def test_unnumbered_still_carries_asn(self):
        command = build_bgp_attach_command(BgpRack(asn=65101, address="fe80::1"))
        assert command == "microovn setup bgp-attach --peer 65101@fe80::1"


class TestMicroOvnBgpAttachStep:
    RACK = "rack1"
    MACHINES = ["compute-0", "compute-1"]
    CONFIG = BgpRack(asn=65101, address="192.0.2.17", networks=["192.0.2.16/28"])

    @pytest.fixture
    def jhelper(self):
        return MagicMock()

    def make_step(self, jhelper, timeout=None):
        return MicroOvnBgpAttachStep(
            jhelper,
            "openstack-machines",
            self.RACK,
            self.CONFIG,
            self.MACHINES,
            timeout=timeout,
        )

    @staticmethod
    def exec_side_effect(attach_task, status_tasks):
        """Return attach result on the attach command, statuses in order.

        Statuses repeat cyclically: the polling loop may re-read status
        more often than there are staged responses.
        """
        statuses = itertools.cycle(status_tasks)

        def side_effect(unit, model, cmd, timeout=None):
            if cmd == bgp.BGP_ATTACH_STATUS_COMMAND:
                return next(statuses)
            return attach_task

        return side_effect

    def test_run_success(self, jhelper, step_context):
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(), [attach_task(stdout=STATUS_ALL_ATTACHED)]
        )
        result = self.make_step(jhelper, timeout=5).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        attach_cmd = jhelper.run_cmd_on_unit.call_args_list[0].args[2]
        assert attach_cmd == (
            "microovn setup bgp-attach --peer 65101@192.0.2.17 --networks 192.0.2.16/28"
        )
        jhelper.run_cmd_on_unit.assert_called_with(
            "microovn/0", "openstack-machines", bgp.BGP_ATTACH_STATUS_COMMAND
        )

    def test_run_attach_command_failure_is_hard(self, jhelper, step_context):
        # Any non-zero attach that is not "matches no router port" fails
        # immediately: no status polling at all.
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.return_value = attach_task(return_code=1, stderr="boom")
        result = self.make_step(jhelper, timeout=5).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "boom" in result.message
        assert jhelper.run_cmd_on_unit.call_count == 1

    def test_run_down_compute_warning_not_fatal(self, jhelper, step_context):
        # A down compute makes bgp-attach exit 0 with a Warning line; the
        # poll decides the outcome.
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(stdout="Warning: compute-1: not reached (down)"),
            [attach_task(stdout=STATUS_ALL_ATTACHED)],
        )
        result = self.make_step(jhelper, timeout=5).run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_timeout_includes_attach_error(self, jhelper, step_context):
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(
                return_code=1,
                stderr="Error: compute-1: matches no router port",
            ),
            [attach_task(stdout=STATUS_PARTIAL)],
        )
        result = self.make_step(jhelper, timeout=0.2).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "compute-1" in result.message
        assert "matches no router port" in result.message

    def test_run_no_router_port_is_soft(self, jhelper, step_context):
        # Right after the agent enablement Neutron is still creating the
        # router ports: attach exits 1 with "matches no router port",
        # the poll reruns the call, and a later iteration converges.
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(
                return_code=1,
                stderr="Error: compute-0: matches no router port",
            ),
            [
                attach_task(stdout=STATUS_PARTIAL),
                attach_task(stdout=STATUS_ALL_ATTACHED),
            ],
        )
        result = self.make_step(jhelper, timeout=5).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        attach_calls = [
            call.args[2]
            for call in jhelper.run_cmd_on_unit.call_args_list
            if call.args[2] != bgp.BGP_ATTACH_STATUS_COMMAND
        ]
        assert len(attach_calls) == 2

    def test_run_timeout_names_down_compute(self, jhelper, step_context):
        # A down compute never becomes attached, so the rack never
        # reaches "in sync": the timeout failure must carry the last
        # Warning line so the operator knows it is a down compute.
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(stdout="Warning: compute-1: not reached (down)"),
            [attach_task(stdout=STATUS_PARTIAL)],
        )
        result = self.make_step(jhelper, timeout=0.2).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "compute-1" in result.message
        assert "not reached (down)" in result.message

    def test_run_polls_until_attached(self, jhelper, step_context):
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(),
            [
                attach_task(stdout=STATUS_PARTIAL),
                attach_task(stdout=STATUS_ALL_ATTACHED),
            ],
        )
        result = self.make_step(jhelper, timeout=5).run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_timeout_lists_missing(self, jhelper, step_context):
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = self.exec_side_effect(
            attach_task(), [attach_task(stdout=STATUS_PARTIAL)]
        )
        result = self.make_step(jhelper, timeout=0.2).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "compute-1" in result.message

    def test_run_exec_exception(self, jhelper, step_context):
        jhelper.get_leader_unit.return_value = "microovn/0"
        jhelper.run_cmd_on_unit.side_effect = RuntimeError("juju is down")
        result = self.make_step(jhelper).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "juju is down" in result.message

    def test_is_skip_never_skips(self, jhelper, step_context):
        # The sync call is idempotent and cheap; a skip cannot detect
        # settings drift (e.g. a changed pool on an all-attached rack).
        result = self.make_step(jhelper).is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.run_cmd_on_unit.assert_not_called()
        jhelper.get_leader_unit.assert_not_called()


class TestSaveMaasRackControllersStep:
    def make_step(self, cclient, maas_client):
        return SaveMaasRackControllersStep(
            cclient, MagicMock(), maas_client=maas_client
        )

    def test_run(self, cclient, step_context):
        maas_client = MagicMock()
        maas_client.list_rack_controllers.return_value = [
            {"hostname": "rack-b", "system_id": "s2"},
            {"hostname": "rack-a", "system_id": "s1"},
            {"system_id": "s3"},  # no hostname: ignored
        ]
        result = self.make_step(cclient, maas_client).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        cclient.cluster.update_config.assert_called_once_with(
            "MaasRackControllers", '{"racks": ["rack-a", "rack-b"]}'
        )

    def test_run_empty_discovery(self, cclient, step_context):
        maas_client = MagicMock()
        maas_client.list_rack_controllers.return_value = []
        result = self.make_step(cclient, maas_client).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "no rack controllers" in result.message

    def test_run_maas_error(self, cclient, step_context):
        maas_client = MagicMock()
        maas_client.list_rack_controllers.side_effect = RuntimeError("boom")
        result = self.make_step(cclient, maas_client).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "boom" in result.message


class TestSetNodeRackNamesStep:
    def test_run_stamps_every_machine(self, cclient, step_context):
        step = SetNodeRackNamesStep(
            cclient,
            {"rack1": ["node-a", "node-b"], "rack2": ["node-c"]},
        )
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        calls = cclient.cluster.update_node_info.call_args_list
        assert len(calls) == 3
        assert calls[0].args == ("node-a",)
        assert calls[0].kwargs == {"rack_name": "rack1"}
        assert calls[1].args == ("node-b",)
        assert calls[2].args == ("node-c",)
        assert calls[2].kwargs == {"rack_name": "rack2"}

    def test_run_node_missing(self, cclient, step_context):
        cclient.cluster.update_node_info.side_effect = Exception("node not found")
        step = SetNodeRackNamesStep(cclient, {"rack1": ["node-x"]})
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "node not found" in result.message


class TestEnableNeutronBgpStep:
    @pytest.fixture
    def deps(self):
        return {
            "client": MagicMock(),
            "manifest": MagicMock(),
            "tfhelper": MagicMock(),
            "jhelper": MagicMock(),
        }

    def test_run(self, deps, step_context):
        step = EnableNeutronBgpStep(
            deps["client"],
            deps["manifest"],
            deps["tfhelper"],
            deps["jhelper"],
            "openstack",
        )
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        deps["tfhelper"].update_tfvars_and_apply_tf.assert_called_once_with(
            deps["client"],
            deps["manifest"],
            tfvar_config="TerraformVarsOpenstack",
            override_tfvars={BGP_ENABLE_TFVAR: True},
        )
        deps["jhelper"].wait_application_ready.assert_called_once_with(
            "neutron", "openstack", timeout=step.NEUTRON_READY_TIMEOUT
        )

    def test_run_apply_failure(self, deps, step_context):
        deps["tfhelper"].update_tfvars_and_apply_tf.side_effect = RuntimeError("boom")
        step = EnableNeutronBgpStep(
            deps["client"],
            deps["manifest"],
            deps["tfhelper"],
            deps["jhelper"],
            "openstack",
        )
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "boom" in result.message

    def test_run_wait_failure(self, deps, step_context):
        deps["jhelper"].wait_application_ready.side_effect = RuntimeError("stuck")
        step = EnableNeutronBgpStep(
            deps["client"],
            deps["manifest"],
            deps["tfhelper"],
            deps["jhelper"],
            "openstack",
        )
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "neutron" in result.message


class TestValidateNeutronBgpPluginStep:
    def test_plugin_present(self, step_context):
        jhelper = MagicMock()
        jhelper.run_cmd_on_unit_payload.return_value = {
            "return-code": 0,
            "stdout": "service_plugins = ovn-router,ovn-bgp\n",
        }
        result = ValidateNeutronBgpPluginStep(jhelper, "openstack").run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_plugin_missing(self, step_context):
        jhelper = MagicMock()
        jhelper.run_cmd_on_unit_payload.return_value = {
            "return-code": 0,
            "stdout": "service_plugins = ovn-router\n",
        }
        result = ValidateNeutronBgpPluginStep(jhelper, "openstack").run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "ovn-bgp" in result.message

    def test_exec_failure(self, step_context):
        jhelper = MagicMock()
        jhelper.run_cmd_on_unit_payload.side_effect = RuntimeError("boom")
        result = ValidateNeutronBgpPluginStep(jhelper, "openstack").run(step_context)
        assert result.result_type == ResultType.FAILED
