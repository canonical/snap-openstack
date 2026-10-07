# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from contextlib import nullcontext
from unittest.mock import Mock

import jubilant.statustypes as status_types
import pytest

from sunbeam.core.juju import ApplicationReadiness, JujuException, JujuHelper


def application(units, status="active", subordinate_to=()):
    return status_types.AppStatus(
        charm="test",
        charm_origin="charmhub",
        charm_name="test",
        charm_rev=1,
        exposed=False,
        units=units,
        app_status=status_types.StatusInfo(current=status),
        subordinate_to=list(subordinate_to),
    )


def unit(machine="0", agent="idle", workload="active", subordinates=None):
    return status_types.UnitStatus(
        machine=machine,
        juju_status=status_types.StatusInfo(current=agent),
        workload_status=status_types.StatusInfo(current=workload),
        subordinates=subordinates or {},
    )


def model(apps):
    return status_types.Status(
        model=status_types.ModelStatus._from_dict(
            {
                "name": "test",
                "controller": "test",
                "cloud": "test",
                "region": "test",
                "type": "iaas",
                "version": "3.6",
            }
        ),
        machines={},
        apps=apps,
    )


@pytest.fixture
def subordinate_status():
    """Three nodes with distinct collector attachments on each machine."""
    apps = {}
    number = 0
    for principal in ("k8s", "microceph", "sunbeam-machine"):
        units = {}
        for machine in range(3):
            units[f"{principal}/{machine}"] = unit(
                str(machine), subordinates={f"collector/{number}": unit()}
            )
            number += 1
        apps[principal] = application(units)
    apps["collector"] = application({}, subordinate_to=list(apps))
    return model(apps)


def test_subordinate_coverage_on_every_principal(subordinate_status):
    requirement = ApplicationReadiness(
        principals={
            principal: ["0", "1", "2"]
            for principal in ("k8s", "microceph", "sunbeam-machine")
        }
    )
    assert requirement.pending(subordinate_status, "collector") == []
    # Eight other idle units must not hide a missing attachment on machine 2.
    subordinate_status.apps["microceph"].units["microceph/2"].subordinates.clear()
    assert any(
        "microceph/2" in message
        for message in requirement.pending(subordinate_status, "collector")
    )


@pytest.mark.parametrize("agent", ["executing", "error", "", "allocating"])
def test_one_unsettled_subordinate_blocks_readiness(subordinate_status, agent):
    parent = subordinate_status.apps["k8s"].units["k8s/1"]
    parent.subordinates["collector/1"] = unit(agent=agent)
    assert any(
        "collector/1: agent" in message
        for message in ApplicationReadiness().pending(subordinate_status, "collector")
    )
    assert not JujuHelper._is_desired_status_achieved(
        subordinate_status.apps["collector"],
        [],
        ["active"],
        ["idle"],
        resolved_units=subordinate_status.get_units("collector"),
    )


@pytest.mark.parametrize(
    "agent,ready", [("idle", True), ("executing", False), ("error", False)]
)
def test_hardware_observer_blocked_exception(agent, ready):
    status = model(
        {
            "machine": application(
                {
                    "machine/0": unit(
                        subordinates={
                            "hardware-observer/0": unit(agent=agent, workload="blocked")
                        }
                    )
                }
            ),
            "hardware-observer": application({}, "blocked", ["machine"]),
        }
    )
    requirement = ApplicationReadiness(
        status=["active", "blocked"], principals={"machine": ["0"]}
    )
    assert (not requirement.pending(status, "hardware-observer")) is ready
    assert ApplicationReadiness().pending(status, "hardware-observer")


def test_missing_and_zero_units_are_explicit():
    status = model({"app": application({})})
    assert ApplicationReadiness().pending(status, "missing")
    assert ApplicationReadiness().pending(status, "app")
    assert ApplicationReadiness(units=0).pending(status, "app") == []
    assert ApplicationReadiness(units=1).pending(status, "app")


@pytest.mark.parametrize("agent,ready", [("idle", True), ("executing", False)])
def test_shared_wait_resolves_subordinate_units(subordinate_status, agent, ready):
    subordinate_status.apps["k8s"].units["k8s/1"].subordinates["collector/1"] = unit(
        agent=agent
    )
    helper = Mock(spec=JujuHelper)
    helper._model.return_value = nullcontext(Mock())
    JujuHelper.wait_until_desired_status(
        helper, "test", ["collector"], agent_status=["idle"]
    )
    predicate = helper._wait.call_args.args[0]
    assert predicate(subordinate_status) is ready


@pytest.mark.parametrize("requirements", [{}, {"test": {}}])
def test_empty_scope_does_not_report_ready(requirements):
    with pytest.raises(ValueError, match="No application readiness targets"):
        JujuHelper.wait_until_models_ready(Mock(spec=JujuHelper), requirements, 60)


def test_placement_and_relation_coverage():
    status = model({"app": application({"app/0": unit()})})
    requirement = ApplicationReadiness(
        machines=["0", "1"], relations={"logging": {"collector"}}
    )
    pending = requirement.pending(status, "app")
    assert any("expected machines" in message for message in pending)
    assert any("expected 2 units" in message for message in pending)
    assert any(
        "logging: relation to collector missing" in message for message in pending
    )


@pytest.fixture
def clock(mocker):
    current = [0.0]
    mocker.patch("sunbeam.core.juju.time.monotonic", side_effect=lambda: current[0])
    mocker.patch(
        "sunbeam.core.juju.time.sleep",
        side_effect=lambda seconds: current.__setitem__(0, current[0] + seconds),
    )
    return current


def test_models_rechecked_until_all_pass_three_rounds(clock):
    ready = model({"app": application({"app/0": unit()})})
    busy = model({"app": application({"app/0": unit(agent="executing")})})
    helper = Mock(spec=JujuHelper)
    helper.get_model_status.side_effect = [
        ready,
        busy,
        busy,
        ready,
        ready,
        ready,
        ready,
        ready,
        ready,
        ready,
    ]
    JujuHelper.wait_until_models_ready(
        helper,
        {
            "first": {"app": ApplicationReadiness()},
            "second": {"app": ApplicationReadiness()},
        },
        timeout=60,
    )
    assert [call.args[0] for call in helper.get_model_status.call_args_list] == [
        "first",
        "second",
    ] * 5
    assert clock[0] == 40


def test_membership_change_restarts_consecutive_successes(clock):
    first = model({"app": application({"app/0": unit()})})
    second = model({"app": application({"app/1": unit()})})
    helper = Mock(spec=JujuHelper)
    helper.get_model_status.side_effect = [first, second, second, second]
    JujuHelper.wait_until_models_ready(
        helper, {"test": {"app": ApplicationReadiness()}}, timeout=60
    )
    assert helper.get_model_status.call_count == 4


def test_failed_sample_restarts_consecutive_successes(clock):
    ready = model({"app": application({"app/0": unit()})})
    helper = Mock(spec=JujuHelper)
    helper.get_model_status.side_effect = [
        ready,
        JujuException("unavailable"),
        ready,
        ready,
        ready,
    ]
    JujuHelper.wait_until_models_ready(
        helper, {"test": {"app": ApplicationReadiness()}}, timeout=60
    )
    assert helper.get_model_status.call_count == 5


def test_timeout_is_shared_and_reports_pending_unit(clock):
    helper = Mock(spec=JujuHelper)
    helper.get_model_status.return_value = model(
        {"app": application({"app/0": unit(agent="executing")})}
    )
    with pytest.raises(TimeoutError, match="first/app/0: agent 'executing'"):
        JujuHelper.wait_until_models_ready(
            helper,
            {
                "first": {"app": ApplicationReadiness()},
                "second": {"app": ApplicationReadiness()},
            },
            timeout=25,
        )
    assert clock[0] == 25
    assert helper.get_model_status.call_count == 6
