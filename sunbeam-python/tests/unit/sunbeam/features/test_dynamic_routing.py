# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test the dynamic routing feature's placement and operator workflow."""

import ipaddress
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import click
import pytest
import yaml
from click.testing import CliRunner
from openstack.exceptions import HttpException, SDKException
from pydantic import ValidationError

from sunbeam.core.deployment import Deployment
from sunbeam.core.terraform import TerraformHelper
from sunbeam.feature_manager import FeatureManager
from sunbeam.features.dynamic_routing import feature as ndr
from sunbeam.features.interface.v1.base import FeatureError

NODES = [
    {"name": "net-01", "machineid": 0, "role": ["network"]},
    {"name": "net-02", "machineid": 1, "role": ["network"], "arch": "arm64"},
    {"name": "compute-01", "machineid": 2, "role": ["compute"]},
]


def trunk(*nodes, family="ipv4", vlan=120):
    """Declare a managed trunk addressing fixture for the selected nodes."""
    addresses = {
        node: {
            "address": f"192.0.2.{offset}/28"
            if family == "ipv4"
            else f"2001:db8::{offset}/64"
        }
        for offset, node in enumerate(nodes, 3)
    }
    return {
        "address-families": [family],
        "peering": {
            "provider-trunk": {"physnet": "physnet1", "vlan": vlan, "nodes": addresses}
        },
    }


ALL_NETWORK_NODES = trunk("net-01", "net-02")
APPENDIX_A = """
features:
  dynamic-routing:
    config:
      router-id-pool: 198.19.0.0/16
      routing-domains:
        red:
          address-families: [ipv4]
          peering:
            provider-trunk:
              physnet: physnet1
              vlan: 120
              nodes:
                net-01: {address: 192.0.2.3/28}
                net-02: {address: 192.0.2.4/28}
        blue:
          address-families: [ipv6]
          peering:
            dedicated-uplink:
              nodes:
                net-01: {}
                net-02: {}
"""


def apply_calls(helper):
    return [
        call for call in helper.run_action.call_args_list if call.args[2] == ndr.ACTION
    ]


def placement(domains):
    return ndr.DynamicRoutingConfig.model_validate({"routing-domains": domains})


def status_unit(machine, subordinates):
    return SimpleNamespace(
        machine=str(machine), subordinates=dict.fromkeys(subordinates)
    )


@pytest.mark.parametrize(
    "config",
    [
        {"routing-domain": {}},
        {"routing-domains": {"red": {**ALL_NETWORK_NODES, "unknown": {}}}},
    ],
)
def test_manifest_rejects_unknown_keys(config):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ndr.DynamicRoutingConfig.model_validate(config)


def test_domain_placement_and_stable_identities():
    """Both domains may run independently on the same host."""
    config = placement(
        {"client1": ALL_NETWORK_NODES, "internet": trunk("net-02", vlan=121)}
    )
    instances = ndr.resolve_instances(config.routing_domains, NODES)

    assert [item["host"] for item in instances["net-01"]] == ["net-01.client1.v4"]
    assert [item["host"] for item in instances["net-02"]] == [
        "net-02.client1.v4",
        "net-02.internet.v4",
    ]
    assert instances["net-01"][0]["service-name"] == "agent-client1-v4"
    assert instances["net-02"][1]["peering"]["provider-trunk"]["vlan"] == 121
    assert ndr.resolve_instances(config.routing_domains, NODES[::-1]) == instances
    ndr.reserve_router_ids(None, {}, {}, instances)
    assert (
        len({item["router-id"] for group in instances.values() for item in group}) == 3
    )


@pytest.mark.parametrize(
    "name,reason",
    [("net-typo", "not registered"), ("compute-01", "does not have the network role")],
)
def test_ineligible_placement_has_context(name, reason):
    """Check exact deployment names and the network role."""
    config = placement({"client1": trunk(name)})
    with pytest.raises(FeatureError, match=rf"client1.*{name}.*{reason}.*net-01"):
        ndr.resolve_instances(config.routing_domains, NODES)


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"nodes": ["net-01"]},
        {"peering": {}},
        {
            "peering": {
                "provider-trunk": {"physnet": "physnet1", "vlan": 120, "nodes": {}}
            }
        },
        {
            "peering": {
                **ALL_NETWORK_NODES["peering"],
                "dedicated-uplink": {"nodes": {"net-01": {}}},
            }
        },
        {**ALL_NETWORK_NODES, "address-families": []},
        {**ALL_NETWORK_NODES, "address-families": ["ipv6", "ipv6"]},
        {**ALL_NETWORK_NODES, "address-families": ["ipv4", "ipv6"]},
        {**ALL_NETWORK_NODES, "address-families": ["ipv6", "ipv4"]},
        {**ALL_NETWORK_NODES, "address-families": ["ipv5"]},
        {**ALL_NETWORK_NODES, "address-families": ["ipv6"]},
    ],
)
def test_invalid_domain_inputs(settings):
    """Reject unusable manifest entries before resolving Juju units."""
    with pytest.raises(ValidationError):
        placement({"client1": settings})


@pytest.fixture
def reconciler(monkeypatch):
    """Provide a named mixed-architecture deployment with a test Neutron API."""
    deployment = Mock(openstack_machines_model="machines", external_keystone_model=None)
    deployment.get_tfhelper.return_value = Mock(spec=TerraformHelper)
    client = deployment.get_client.return_value
    client.cluster.list_nodes.return_value = NODES
    deployment.get_manifest.return_value.core.software.charms = {}
    helper = Mock()
    deployment.get_juju_helper.return_value = helper
    helper.get_model_status.return_value.apps = {
        "neutron": SimpleNamespace(
            relations={"ovsdb-cms": [SimpleNamespace(related_app="sunbeam-ovn-proxy")]}
        ),
        "microovn": SimpleNamespace(
            units={"microovn/0": status_unit(0, ["openstack-network-agents/0"])}
        ),
        "microovn-arm64": SimpleNamespace(
            units={
                "microovn-arm64/0": status_unit(1, ["openstack-network-agents-arm64/0"])
            }
        ),
    }
    helper.get_application_actions.return_value = [
        ndr.ACTION,
        ndr.VALIDATE_ACTION,
    ]

    def run_action(unit, model, action, params=None, **kwargs):
        if action == ndr.VALIDATE_ACTION:
            assert params["contract-version"] == 1
            instances = json.loads(params["instances"])
            for instance in instances:
                uplink = instance["peering"].get("dedicated-uplink")
                if uplink is not None:
                    uplink.setdefault(
                        "address",
                        "2001:db8::3" if unit.endswith("/0") else "2001:db8::4",
                    )
            return {**params, "instances": json.dumps(instances), "amqp-ready": True}
        assert action == ndr.ACTION
        assert params["contract-version"] == 1
        return {"configured": True}

    helper.run_action.side_effect = run_action
    helper.get_app_config.return_value = {ndr.NEUTRON_OPTION: False}
    deployment.get_tfhelper.return_value.output.return_value = {
        "rabbitmq-offer-url": "admin/openstack.rabbitmq"
    }
    network = MagicMock()
    network.bgp_speakers.side_effect = lambda **_: iter(())
    network.extensions.return_value = [
        SimpleNamespace(alias="bgp"),
        SimpleNamespace(alias="bgp_dragent_scheduler"),
    ]
    session = MagicMock()
    session.__enter__.return_value.network = network
    monkeypatch.setattr(ndr, "JujuHelper", Mock(return_value=helper))
    monkeypatch.setattr(ndr, "get_admin_connection", Mock(return_value=session))
    monkeypatch.setattr(ndr, "run_preflight_checks", Mock())
    monkeypatch.setattr(ndr, "refresh_machine_collector_integrations", Mock())

    feature = ndr.DynamicRoutingFeature()
    state = {}
    monkeypatch.setattr(feature, "get_feature_info", lambda client: dict(state))
    monkeypatch.setattr(
        feature, "update_feature_info", lambda client, data: state.update(data)
    )
    monkeypatch.setattr(feature, "wait_for_agents", Mock())
    return feature, deployment, helper, network, state


def test_read_back_saved_placement_if_action_fails(reconciler):
    feature, deployment, helper, _, state = reconciler
    desired = ndr.resolve_instances(
        placement({"red": trunk("net-01")}).routing_domains, NODES
    )
    ndr.reserve_router_ids(None, {}, {}, desired)
    helper.run_action.side_effect = [
        ndr.ActionFailedException("Action response timed out"),
        {"saved-instances": json.dumps(desired["net-01"])},
    ]

    with pytest.raises(FeatureError, match="saved its placement.*readiness"):
        feature.apply_units(
            deployment.get_client(),
            helper,
            "machines",
            {"net-01": "openstack-network-agents/0"},
            {},
            desired,
        )

    assert helper.run_action.call_args.args[2] == ndr.VALIDATE_ACTION
    assert state["instances"] == desired


def test_failed_action_restores_confirmed_previous_placement(reconciler):
    feature, deployment, helper, _, state = reconciler
    desired = ndr.resolve_instances(
        placement({"red": trunk("net-01")}).routing_domains, NODES
    )
    ndr.reserve_router_ids(None, {}, {}, desired)
    helper.run_action.side_effect = [
        ndr.ActionFailedException("Action failed before saving"),
        {"saved-instances": "[]"},
    ]

    with pytest.raises(ndr.ActionFailedException, match="before saving"):
        feature.apply_units(
            deployment.get_client(),
            helper,
            "machines",
            {"net-01": "openstack-network-agents/0"},
            {},
            desired,
        )

    assert state["instances"] == {}


def test_enable_waits_for_amqp_credentials_before_placing_agents(
    reconciler, monkeypatch
):
    feature, deployment, helper, _, state = reconciler
    mocked_action = helper.run_action.side_effect
    tfhelper = deployment.get_tfhelper.return_value
    probes = []

    def run_action(unit, model, action, params=None, **kwargs):
        if action == ndr.VALIDATE_ACTION and tfhelper.update_tfvars_and_apply_tf.called:
            probes.append(unit)
            return {"amqp-ready": len(probes) == 2}
        if action == ndr.ACTION:
            assert len(probes) == 2
        return mocked_action(unit, model, action, params)

    helper.run_action.side_effect = run_action
    monkeypatch.setattr(ndr, "wait_fixed", lambda seconds: lambda _: 0)

    feature.apply_placement(deployment, placement({"red": trunk("net-01")}))

    assert probes == ["openstack-network-agents/0"] * 2
    assert len(state["instances"]["net-01"]) == 1
    assert tfhelper.mock_calls[:2] == [call.init(), call.output()]
    assert tfhelper.mock_calls.index(call.init(), 2) < next(
        index
        for index, invocation in enumerate(tfhelper.mock_calls)
        if invocation[0] == "update_tfvars_and_apply_tf"
    )


@pytest.mark.parametrize("shared_keystone", [False, True])
def test_reconcile_and_force_removal(reconciler, capsys, shared_keystone):
    """Removal refuses a bound speaker until the operator overrides explicitly."""
    feature, deployment, helper, network, state = reconciler
    if shared_keystone:
        deployment.external_keystone_model = "region-controller:admin/openstack"
        deployment.get_juju_helper.return_value = Mock(name="region_controller")
    keystone_helper = deployment.get_juju_helper.return_value
    region = "secondary" if shared_keystone else "primary"
    deployment.get_region_name.return_value = region
    admin_connection = call(keystone_helper, deployment, region_name=region)

    feature.apply_placement(
        deployment,
        placement(
            {"client1": ALL_NETWORK_NODES, "internet": trunk("net-02", vlan=121)}
        ),
    )
    helper.set_app_config.assert_called_once_with(
        "neutron", ndr.OPENSTACK_MODEL, {ndr.NEUTRON_OPTION: True}
    )
    assert deployment.get_tfhelper.return_value.update_tfvars.call_args.kwargs[
        "override_tfvars"
    ] == {"neutron-config": {ndr.NEUTRON_OPTION: True}}
    assert (
        deployment.get_tfhelper.return_value.update_tfvars_and_apply_tf.call_args.kwargs[
            "tf_apply_extra_args"
        ]
        == ["-target=juju_integration.amqp"]
    )
    old = state["instances"]
    assert [call.args[0] for call in apply_calls(helper)] == [
        "openstack-network-agents/0",
        "openstack-network-agents-arm64/0",
    ]
    assert json.loads(helper.run_action.call_args.args[3]["instances"]) == old["net-02"]
    deployment.get_juju_helper.assert_called_once_with(keystone=True)
    assert ndr.get_admin_connection.call_args_list == [admin_connection] * 2
    feature.wait_for_agents.assert_called_once_with(
        deployment,
        keystone_helper,
        {"net-01.client1.v4", "net-02.client1.v4", "net-02.internet.v4"},
    )

    helper.run_action.reset_mock()
    helper.set_app_config.reset_mock()
    terraform = deployment.get_tfhelper.return_value.update_tfvars_and_apply_tf
    terraform.reset_mock()
    ndr.get_admin_connection.reset_mock()
    network.agents.return_value = [
        SimpleNamespace(host="net-02.internet.v4", id="agent")
    ]
    network.get_bgp_speakers_hosted_by_dragent.return_value = [
        SimpleNamespace(name="internet", id="speaker")
    ]
    update = placement({"client1": ALL_NETWORK_NODES})
    with pytest.raises(FeatureError, match="--force"):
        feature.apply_placement(deployment, update)
    assert state["instances"] == old
    assert not apply_calls(helper)
    helper.set_app_config.assert_not_called()
    terraform.assert_not_called()
    assert ndr.get_admin_connection.call_args_list == [admin_connection]
    assert "hosts speaker internet" in capsys.readouterr().err

    feature.apply_placement(deployment, update, force=True)
    assert "hosts speaker internet" in capsys.readouterr().err
    assert json.loads(helper.run_action.call_args.args[3]["instances"]) == [
        old["net-02"][0]
    ]
    network.remove_bgp_speaker_from_dragent.assert_not_called()

    ndr.get_admin_connection.reset_mock()
    helper.set_app_config.reset_mock()
    feature.apply_placement(deployment, ndr.DynamicRoutingConfig(), enabled=False)
    assert ndr.get_admin_connection.call_args_list == [admin_connection]
    helper.set_app_config.assert_called_once_with(
        "neutron", ndr.OPENSTACK_MODEL, {ndr.NEUTRON_OPTION: False}
    )
    assert not state["instances"]


def test_force_does_not_bypass_failed_binding_lookup(reconciler):
    """Fail closed if a removal cannot be checked against Neutron."""
    feature, deployment, helper, network, state = reconciler
    feature.apply_placement(deployment, placement({"client1": ALL_NETWORK_NODES}))
    helper.run_action.reset_mock()
    network.agents.side_effect = SDKException("unavailable")

    with pytest.raises(FeatureError, match="Unable to check Neutron speaker bindings"):
        feature.apply_placement(deployment, placement({}), force=True)

    assert state["instances"]
    assert not apply_calls(helper)


def test_missing_subordinate_blocks_all_changes(reconciler):
    """Resolve both architectures and refuse a missing unit before applying."""
    feature, deployment, helper, _, state = reconciler
    helper.get_model_status.return_value.apps.pop("microovn-arm64")

    with pytest.raises(FeatureError, match="net-02.*openstack-network-agents-arm64"):
        feature.apply_placement(deployment, placement({"client1": ALL_NETWORK_NODES}))

    assert not state
    helper.run_action.assert_not_called()
    deployment.get_tfhelper.return_value.update_tfvars_and_apply_tf.assert_not_called()


def test_placement_lock_conflict_does_not_mutate_state(reconciler):
    """Reject a concurrent operation before reading or applying its placement."""
    feature, deployment, helper, _, state = reconciler
    cluster = deployment.get_client().cluster
    cluster.lock_terraform_plan.side_effect = ndr.TerraformPlanLockConflictException(
        "another owner"
    )
    with pytest.raises(FeatureError, match="placement change is in progress"):
        feature.apply_placement(deployment, placement({"client1": ALL_NETWORK_NODES}))

    cluster.unlock_terraform_plan.assert_not_called()
    helper.run_action.assert_not_called()
    assert not state


def test_paused_unit_does_not_report_success(reconciler):
    """A prior heartbeat cannot turn a paused configure action into success."""
    feature, deployment, helper, _, state = reconciler
    mocked_action = helper.run_action.side_effect
    helper.run_action.side_effect = lambda unit, model, action, params=None, **kwargs: (
        {"configured": False}
        if action == ndr.ACTION
        else mocked_action(unit, model, action, params)
    )
    config = placement({"client1": trunk("net-01")})

    with pytest.raises(FeatureError, match=r"readiness.*peering.*AMQP"):
        feature.apply_placement(deployment, config)

    feature.wait_for_agents.assert_not_called()
    assert state["instances"]["net-01"][0]["host"] == "net-01.client1.v4"
    cluster = deployment.get_client().cluster
    assert (
        cluster.unlock_terraform_plan.call_args == cluster.lock_terraform_plan.call_args
    )


def test_wait_for_agents_requires_a_new_heartbeat(reconciler, monkeypatch):
    """An alive record from before the apply cannot satisfy readiness."""
    feature, deployment, helper, network, _ = reconciler
    host = "net-01.client1.v4"
    old = SimpleNamespace(host=host, is_alive=True, last_heartbeat_at="old")
    new = SimpleNamespace(host=host, is_alive=True, last_heartbeat_at="new")
    network.agents.side_effect = [[old], [old], [new]]
    wait_fixed = ndr.wait_fixed
    monkeypatch.setattr(ndr, "wait_fixed", lambda _: wait_fixed(0))

    ndr.DynamicRoutingFeature.wait_for_agents(feature, deployment, helper, {host})

    assert network.agents.call_count == 3


def test_ovn_k8s_blocks_enable_before_changes(reconciler):
    """Reject a Kubernetes OVN control plane even if MicroOVN units exist."""
    feature, deployment, helper, _, state = reconciler
    helper.get_model_status.return_value.apps["neutron"].relations = {
        "ovsdb-cms": [SimpleNamespace(related_app="ovn-central")]
    }

    with pytest.raises(FeatureError, match="requires MicroOVN"):
        feature.apply_placement(
            deployment, placement({"client1": ALL_NETWORK_NODES}), force=True
        )

    assert not state
    helper.set_app_config.assert_not_called()
    helper.run_action.assert_not_called()
    deployment.get_tfhelper.assert_not_called()


def test_retry_reuses_declared_domains(reconciler):
    """An interrupted first apply must leave the desired domains available on retry."""
    feature, deployment, helper, _, state = reconciler
    mocked_action = helper.run_action.side_effect

    def interrupt_apply(unit, model, action, params=None, **kwargs):
        if action == ndr.ACTION:
            raise ndr.FeatureError("action interrupted")
        return mocked_action(unit, model, action, params)

    helper.run_action.side_effect = interrupt_apply
    with pytest.raises(FeatureError, match="interrupted"):
        feature.apply_placement(deployment, placement({"client1": ALL_NETWORK_NODES}))

    saved_ids = state["router-ids"].copy()
    helper.run_action.side_effect = mocked_action
    feature.apply_placement(deployment, ndr.DynamicRoutingConfig())

    assert state["router-ids"] == saved_ids
    assert set(state["instances"]) == {"net-01", "net-02"}


def test_router_id_collision_and_exhaustion():
    """Keep a reservation when the next identity picks the same first ID."""
    nodes = [
        {"name": f"net-{number:04}", "machineid": number, "role": ["network"]}
        for number in (108, 639)
    ]
    one = placement({"red": trunk("net-0108")})
    original = ndr.resolve_instances(one.routing_domains, nodes)
    state = ndr.reserve_router_ids(None, {}, {}, original)
    assert original["net-0108"][0]["router-id"] == "198.19.141.44"

    both = placement({"red": trunk("net-0108", "net-0639")})
    desired = ndr.resolve_instances(both.routing_domains, nodes)
    added = ndr.reserve_router_ids(None, state, {}, desired)
    assert added["router-ids"] == {
        "net-0108.red.v4": "198.19.141.44",
        "net-0639.red.v4": "198.19.141.45",
    }

    small_pool = ipaddress.IPv4Network("192.0.2.0/30")
    requested = {"net-01": [ndr.agent_instance("red", "ipv4", "net-01")]}
    depleted = {
        "router-id-pool": str(small_pool),
        "router-ids": {"drained-a": "192.0.2.1", "drained-b": "192.0.2.2"},
    }
    with pytest.raises(FeatureError, match="pool .* is exhausted"):
        ndr.reserve_router_ids(small_pool, depleted, {}, requested)


def test_pool_change_requires_disable_and_retains_same_pool(reconciler):
    """Keep the custom pool on a retry and block changing it while enabled."""
    feature, deployment, helper, _, state = reconciler
    config = ndr.DynamicRoutingConfig.model_validate(
        {"router-id-pool": "198.19.0.0/16", "routing-domains": {"red": trunk("net-01")}}
    )
    feature.apply_placement(deployment, config)
    assignments = state["router-ids"].copy()
    feature.apply_placement(deployment, ndr.DynamicRoutingConfig())
    assert state["router-id-pool"] == "198.19.0.0/16"
    assert state["router-ids"] == assignments

    altered = config.model_copy(
        update={"router_id_pool": ipaddress.IPv4Network("198.18.0.0/16")}
    )
    helper.run_action.reset_mock()
    with pytest.raises(FeatureError, match="Disable dynamic routing before changing"):
        feature.apply_placement(deployment, altered)
    helper.run_action.assert_not_called()

    feature.apply_placement(deployment, ndr.DynamicRoutingConfig(), enabled=False)
    assert state["router-ids"] == assignments
    feature.apply_placement(deployment, altered)
    assert state["router-id-pool"] == "198.18.0.0/16"
    assert state["router-ids"] != assignments


def test_duplicate_declared_source_on_provider_segment():
    """The same L2 placement must not reuse a source across hosts."""
    declaration = trunk("net-01", "net-02")
    nodes = declaration["peering"]["provider-trunk"]["nodes"]
    nodes["net-02"]["address"] = nodes["net-01"]["address"]
    with pytest.raises(FeatureError, match="duplicate peering address"):
        ndr.resolve_instances(placement({"red": declaration}).routing_domains, NODES)


def test_missing_or_ambiguous_maas_uplink_tag():
    """Resolve dedicated interface tags without accepting ambiguous results."""
    domains = placement(
        {"blue": {"peering": {"dedicated-uplink": {"nodes": {"net-01": {}}}}}}
    ).routing_domains
    with pytest.raises(FeatureError, match="explicit.*interface in Manual"):
        ndr.resolve_instances(domains, NODES)

    tagged = {"name": "enp5s0.122", "tags": ["ndr:blue"]}
    expected = ndr.resolve_instances(domains, NODES, {"net-01": [tagged]})
    assert expected["net-01"][0]["peering"] == {
        "dedicated-uplink": {"interface": "enp5s0.122"}
    }
    for interfaces in ([], [tagged, {**tagged, "name": "enp5s0.123"}]):
        with pytest.raises(FeatureError, match="exactly one MAAS interface.*ndr:blue"):
            ndr.resolve_instances(domains, NODES, {"net-01": interfaces})


@pytest.mark.parametrize("node", ["net-01", "net-02"])
def test_changed_attachment_requires_drain(reconciler, node):
    """A changed managed VLAN cannot retain an agent without a drain."""
    feature, deployment, helper, _, state = reconciler
    feature.apply_placement(deployment, placement({"red": trunk("net-01")}))
    helper.run_action.reset_mock()
    helper.set_app_config.reset_mock()
    previous_placement = state["instances"]
    with pytest.raises(FeatureError, match="net-01.red.v4.*Drain"):
        feature.apply_placement(
            deployment, placement({"red": trunk(node, vlan=121)}), force=True
        )
    helper.run_action.assert_not_called()
    helper.set_app_config.assert_not_called()
    assert state["instances"] == previous_placement


def test_changed_discovered_source_requires_drain(reconciler):
    """Detect a change to a dedicated source omitted from the manifest."""
    feature, deployment, helper, _, state = reconciler
    config = placement(
        {
            "blue": {
                "address-families": ["ipv6"],
                "peering": {
                    "dedicated-uplink": {"nodes": {"net-01": {"interface": "eth1"}}}
                },
            }
        }
    )
    feature.apply_placement(deployment, config)
    helper.run_action.reset_mock()
    helper.set_app_config.reset_mock()
    original = helper.run_action.side_effect

    def changed_address(unit, model, action, params=None, **kwargs):
        result = original(unit, model, action, params)
        if action == ndr.VALIDATE_ACTION:
            instances = json.loads(result["instances"])
            instances[0]["peering"]["dedicated-uplink"]["address"] = "2001:db8::9"
            result["instances"] = json.dumps(instances)
        return result

    helper.run_action.side_effect = changed_address
    with pytest.raises(FeatureError, match="net-01.blue.v6.*Drain"):
        feature.apply_placement(deployment, config)
    assert not apply_calls(helper)
    helper.set_app_config.assert_not_called()
    assert state["instances"]["net-01"][0]["peering"] == {
        "dedicated-uplink": {"interface": "eth1", "address": "2001:db8::3"}
    }


def test_invalid_contract_payload_is_rejected_before_juju_preflight(reconciler):
    """Reject a link-local peering source before contacting the unit."""
    feature, deployment, helper, _, state = reconciler
    domain = trunk("net-01")
    domain["peering"]["provider-trunk"]["nodes"]["net-01"]["address"] = "169.254.1.3/24"

    with pytest.raises(FeatureError, match=r"agents/0.*address.*usable"):
        feature.apply_placement(deployment, placement({"red": domain}))

    helper.run_action.assert_not_called()
    helper.set_app_config.assert_not_called()
    assert not state


def test_failed_unit_preflight_blocks_all_mutation(reconciler):
    """Host preflight must succeed on every selected unit before applies."""
    feature, deployment, helper, _, state = reconciler
    original = helper.run_action.side_effect

    def reject_last_unit(unit, model, action, params=None, **kwargs):
        if unit == "openstack-network-agents-arm64/0" and action == ndr.VALIDATE_ACTION:
            raise ndr.ActionFailedException("reserved port")
        return original(unit, model, action, params)

    helper.run_action.side_effect = reject_last_unit
    with pytest.raises(FeatureError, match="agents-arm64/0.*reserved port"):
        feature.apply_placement(deployment, placement({"red": ALL_NETWORK_NODES}))

    assert not state
    assert not apply_calls(helper)
    helper.set_app_config.assert_not_called()
    deployment.get_tfhelper.assert_not_called()


def test_neutron_extensions_gate_agent_start(reconciler, monkeypatch):
    """Require both NDR extensions before starting DR agents."""
    feature, deployment, helper, network, state = reconciler
    selection = placement({"red": trunk("net-01")})
    network.extensions.return_value = [SimpleNamespace(alias="bgp")]
    stop_after_delay = ndr.stop_after_delay
    monkeypatch.setattr(ndr, "stop_after_delay", lambda _: stop_after_delay(0))
    with pytest.raises(
        FeatureError, match="missing NDR extensions: bgp_dragent_scheduler"
    ):
        feature.apply_placement(deployment, selection)
    assert not apply_calls(helper)
    deployment.get_tfhelper.return_value.update_tfvars_and_apply_tf.assert_not_called()
    assert state["router-ids"]
    network.bgp_speakers.assert_not_called()


def test_neutron_bgp_speaker_query_failure_blocks_agent_start(reconciler):
    """Require a successful BGP speaker API request before configuring agents."""
    feature, deployment, helper, network, _ = reconciler

    def failing_speaker_list(*, limit):
        assert limit == 1
        yield from ()
        raise HttpException(http_status=500)

    network.bgp_speakers.side_effect = failing_speaker_list
    with pytest.raises(FeatureError, match=r"BGP API check failed.*500"):
        feature.apply_placement(deployment, placement({"red": trunk("net-01")}))

    assert not apply_calls(helper)
    deployment.get_tfhelper.return_value.update_tfvars_and_apply_tf.assert_not_called()


def test_enable_waits_for_neutron_api_after_plugin_restart(reconciler, monkeypatch):
    """Allow a transient HTTP 503 while the Neutron charm restarts."""
    feature, deployment, helper, network, state = reconciler
    wait_fixed = ndr.wait_fixed
    monkeypatch.setattr(ndr, "wait_fixed", lambda _: wait_fixed(0))
    network.agents.side_effect = [HttpException(http_status=503), []]
    network.extensions.side_effect = [
        HttpException(http_status=503),
        [],
        network.extensions.return_value,
        network.extensions.return_value,
    ]
    network.bgp_speakers.side_effect = [HttpException(http_status=503), iter(())]

    feature.apply_placement(deployment, placement({"client1": trunk("net-01")}))

    assert network.agents.call_count == 2
    assert network.extensions.call_count == 4
    assert network.bgp_speakers.call_count == 2
    assert state["instances"]["net-01"][0]["host"] == "net-01.client1.v4"
    assert len(apply_calls(helper)) == 1


def test_reconcile_mixed_families_and_require_drain(reconciler):
    """Allow mixed domains and require a separate apply to drain a family."""
    feature, deployment, helper, network, state = reconciler
    ipv4 = placement({"client1": trunk("net-01")})
    feature.apply_placement(deployment, ipv4)
    original = state["instances"]["net-01"][0]

    mixed = placement(
        {
            "client1": trunk("net-01"),
            "internet": trunk("net-01", family="ipv6", vlan=121),
        }
    )
    feature.apply_placement(deployment, mixed)

    ipv4_agent, ipv6_agent = state["instances"]["net-01"]
    assert ipv4_agent == original
    assert ipv6_agent["host"] == "net-01.internet.v6"
    assert ipv6_agent["router-id"] != original["router-id"]

    change_family = placement(
        {"client1": trunk("net-01"), "internet": trunk("net-02", vlan=121)}
    )
    prior_state = dict(state)
    helper.run_action.reset_mock()
    helper.set_app_config.reset_mock()
    deployment.get_tfhelper.reset_mock()
    for force in (False, True):
        with pytest.raises(FeatureError, match="internet.*drain"):
            feature.apply_placement(deployment, change_family, force=force)

    assert state == prior_state
    helper.run_action.assert_not_called()
    helper.set_app_config.assert_not_called()
    deployment.get_tfhelper.assert_not_called()

    network.agents.return_value = [
        SimpleNamespace(host=ipv6_agent["host"], id="agent-v6")
    ]
    network.get_bgp_speakers_hosted_by_dragent.return_value = [
        SimpleNamespace(name="internet", id="speaker-v6")
    ]
    with pytest.raises(FeatureError, match="--force"):
        feature.apply_placement(deployment, ipv4)
    assert not apply_calls(helper)

    network.get_bgp_speakers_hosted_by_dragent.return_value = []
    mocked_action = helper.run_action.side_effect

    def interrupt_apply(unit, model, action, params=None, **kwargs):
        if action == ndr.ACTION:
            raise FeatureError("drain interrupted")
        return mocked_action(unit, model, action, params)

    helper.run_action.side_effect = interrupt_apply
    with pytest.raises(FeatureError, match="drain interrupted"):
        feature.apply_placement(deployment, ipv4)
    with pytest.raises(FeatureError, match="internet.*drain"):
        feature.apply_placement(deployment, change_family, force=True)

    helper.run_action.side_effect = mocked_action
    feature.apply_placement(deployment, ipv4)
    payload = json.loads(helper.run_action.call_args.args[3]["instances"])
    assert payload == [original]
    assert state["instances"] == {"net-01": [original]}

    feature.apply_placement(deployment, change_family)
    assert state["instances"]["net-01"] == [original]
    assert [agent["host"] for agent in state["instances"]["net-02"]] == [
        "net-02.internet.v4"
    ]


def test_legacy_placement_requires_binding_check_before_renaming(reconciler, capsys):
    """Report old bindings before replacing a prototype host identity."""
    feature, deployment, helper, network, state = reconciler
    state.update(
        {
            "enabled": "true",
            "routing-domains": {"client1": {"nodes": ["net-01"]}},
            "instances": {
                "net-01": [
                    {
                        **ndr.agent_instance("client1", "ipv4", "net-01"),
                        "host": "agent-client1-v4-net-01",
                        "router-id": "198.18.0.1",
                    }
                ]
            },
        }
    )

    network.agents.return_value = [
        SimpleNamespace(host="agent-client1-v4-net-01", id="legacy-agent")
    ]
    network.get_bgp_speakers_hosted_by_dragent.return_value = [
        SimpleNamespace(name="legacy-speaker", id="speaker")
    ]
    with pytest.raises(FeatureError, match="Drain legacy domains"):
        feature.apply_placement(deployment, ndr.DynamicRoutingConfig())
    helper.run_action.assert_not_called()
    with pytest.raises(FeatureError, match="--force"):
        feature.apply_placement(deployment, placement({}))
    assert not apply_calls(helper)
    assert "hosts speaker legacy-speaker" in capsys.readouterr().err

    network.get_bgp_speakers_hosted_by_dragent.return_value = []
    feature.apply_placement(deployment, placement({}))
    feature.apply_placement(deployment, placement({"client1": trunk("net-01")}))
    assert state["instances"]["net-01"][0]["host"] == "net-01.client1.v4"
    assert state["router-ids"]["agent-client1-v4-net-01"] == "198.18.0.1"
    network.remove_bgp_speaker_from_dragent.assert_not_called()


def test_saved_dual_stack_placement_can_be_drained(reconciler):
    """Keep legacy state usable for explicit drain and reapply."""
    feature, deployment, helper, _, state = reconciler
    state.update(
        {
            "enabled": "true",
            "routing-domains": {
                "client1": {"nodes": ["net-01"], "address-families": ["ipv4", "ipv6"]}
            },
            "instances": {
                "net-01": [
                    {
                        **ndr.agent_instance("client1", family, "net-01"),
                        "host": f"agent-client1-v{family[-1]}-net-01",
                        "router-id": "198.18.0.1" if family == "ipv4" else "198.18.0.2",
                    }
                    for family in ("ipv4", "ipv6")
                ]
            },
        }
    )

    with pytest.raises(FeatureError, match="Drain legacy domains"):
        feature.apply_placement(deployment, ndr.DynamicRoutingConfig())
    helper.run_action.assert_not_called()

    feature.apply_placement(deployment, placement({}))
    assert state["instances"] == {}
    assert json.loads(helper.run_action.call_args.args[3]["instances"]) == []

    feature.apply_placement(deployment, placement({"client1": trunk("net-01")}))
    assert state["instances"]["net-01"][0]["host"] == "net-01.client1.v4"


def test_dynamic_routing_cli_without_gate(reconciler, monkeypatch, tmp_path, snap):
    """Discover, enable and disable dynamic routing without a snap setting."""
    from sunbeam.main import enable

    feature, deployment, helper, _, state = reconciler
    manager = FeatureManager()
    monkeypatch.setattr(manager, "features", lambda: {"dynamic-routing": feature})
    monkeypatch.setattr(manager, "groups", dict)
    deployment.get_client.return_value.cluster.get_feature_gate.return_value = None
    snap.config.get.return_value = False
    monkeypatch.setattr("sunbeam.feature_manager.Snap", lambda: snap)
    manifest_file = tmp_path / "placement.yaml"
    manifest_file.write_text(APPENDIX_A)
    monkeypatch.setattr(ndr, "is_maas_deployment", lambda _: True)
    monkeypatch.setattr(ndr, "MaasClient", Mock())
    list_machines = Mock(
        return_value=[
            {
                "hostname": node["name"],
                "nics": [{"name": "enp5s0.122", "tags": ["ndr:blue"]}],
            }
            for node in NODES[:2]
        ]
    )
    monkeypatch.setattr(ndr, "list_machines", list_machines)
    deployment.get_manifest.return_value = Deployment(
        name="prototype", url="local", type="local"
    ).parse_manifest(yaml.safe_load(manifest_file.read_text()))

    @click.group("init")
    def cli():
        pass

    # Keep the production enable group's options without modifying its command registry.
    enable_group = click.Group(
        enable.name, params=enable.params, callback=enable.callback
    )
    cli.add_command(enable_group)
    cli.add_command(click.Group("disable"))
    manager.register(cli, deployment)

    runner = CliRunner()
    help_result = runner.invoke(
        cli, ["enable", "dynamic-routing", "--help"], obj=deployment
    )
    assert help_result.exit_code == 0, help_result.output
    disable_help = runner.invoke(
        cli, ["disable", "dynamic-routing", "--help"], obj=deployment
    )
    assert disable_help.exit_code == 0, disable_help.output
    for result in (help_result, disable_help):
        assert "Retain their bindings." in " ".join(result.output.split())
    assert state == {}
    helper.run_action.assert_not_called()

    result = runner.invoke(
        cli,
        ["enable", "--manifest", str(manifest_file), "dynamic-routing"],
        obj=deployment,
    )
    assert result.exit_code == 0, result.output
    assert deployment.get_manifest.call_args_list[0].args == (manifest_file,)
    assert set(state["instances"]) == {"net-01", "net-02"}
    assert len(apply_calls(helper)) == 2
    assert list_machines.call_args.kwargs == {"hostname": ["net-01", "net-02"]}
    assert state["router-id-pool"] == "198.19.0.0/16"
    assert state["instances"]["net-01"][0]["peering"] == {
        "dedicated-uplink": {"interface": "enp5s0.122", "address": "2001:db8::3"}
    }

    # With no explicit path, a new cluster manifest is still authoritative.
    updated = ndr.DynamicRoutingConfig.model_validate({"routing-domains": {}})
    deployment.get_manifest.return_value.get_feature("dynamic-routing").config = updated
    result = runner.invoke(cli, ["enable", "dynamic-routing"], obj=deployment)
    assert result.exit_code == 0, result.output
    assert state["instances"] == {}

    result = runner.invoke(
        cli, ["disable", "dynamic-routing", "--force"], obj=deployment
    )
    assert result.exit_code == 0, result.output
    assert state["enabled"] == "false"
    assert state["instances"] == {}


def test_returning_agent_reports_binding_before_start(reconciler, capsys):
    """Re-adding an identity may activate a retained operator binding."""
    feature, deployment, helper, network, _ = reconciler
    network.agents.return_value = [
        SimpleNamespace(host="net-01.client1.v4", id="previous-agent")
    ]
    network.get_bgp_speakers_hosted_by_dragent.return_value = [
        SimpleNamespace(name="client1", id="existing-speaker")
    ]

    mocked_action = helper.run_action.side_effect

    def confirm_notification(unit, model, action, params=None, **kwargs):
        if action == ndr.ACTION and not apply_calls(helper)[:-1]:
            assert "retained binding may resume advertising" in capsys.readouterr().err
        return mocked_action(unit, model, action, params)

    helper.run_action.side_effect = confirm_notification
    feature.apply_placement(deployment, placement({"client1": ALL_NETWORK_NODES}))
    assert len(apply_calls(helper)) == 2
