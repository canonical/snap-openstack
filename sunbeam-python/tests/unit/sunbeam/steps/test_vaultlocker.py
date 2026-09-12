# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Vaultlocker deployment before encrypted storage enrollment."""

from dataclasses import replace
from unittest import mock

import pytest
from jubilant.statustypes import AppStatus, AppStatusRelation, StatusInfo, UnitStatus
from rich.console import Console

from sunbeam.core.common import Result, ResultType
from sunbeam.steps.hypervisor import (
    APPLICATION,
    DeployVaultlockerStep,
    ReapplyHypervisorTerraformPlanStep,
)

VAULTLOCKER = "vaultlocker-hypervisor"


@pytest.fixture
def step():
    return DeployVaultlockerStep(
        mock.MagicMock(),
        mock.MagicMock(),
        mock.MagicMock(),
        mock.MagicMock(),
        "machine-model",
        vault_offer_url="vault.vault-kv",
    )


@pytest.fixture
def ready_status():
    def app(**kwargs):
        return AppStatus(
            charm="test",
            charm_origin="charmhub",
            charm_name="test",
            charm_rev=1,
            exposed=False,
            **kwargs,
        )

    units = {
        f"{APPLICATION}/{index}": UnitStatus(
            machine=str(index),
            juju_status=StatusInfo(current="idle"),
            subordinates={
                f"{VAULTLOCKER}/{index}": UnitStatus(
                    workload_status=StatusInfo(current="active"),
                    juju_status=StatusInfo(current="idle"),
                )
            },
        )
        for index in range(2)
    }
    return mock.Mock(
        apps={
            APPLICATION: app(units=units),
            VAULTLOCKER: app(
                app_status=StatusInfo(current="active"),
                subordinate_to=[APPLICATION],
                relations={
                    "encrypted-device": [AppStatusRelation(related_app=APPLICATION)],
                    "vault-kv": [AppStatusRelation(related_app="vault")],
                },
            ),
        }
    )


def test_ready_checks_nested_subordinates(ready_status):
    assert not ready_status.apps[VAULTLOCKER].units
    assert DeployVaultlockerStep._is_vaultlocker_ready(ready_status)


@pytest.mark.parametrize("endpoint", ["encrypted-device", "vault-kv"])
def test_not_ready_without_relation(ready_status, endpoint):
    ready_status.apps[VAULTLOCKER].relations[endpoint].clear()
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status)


def test_not_ready_until_every_principal_has_subordinate(ready_status):
    ready_status.apps[APPLICATION].units[f"{APPLICATION}/1"].subordinates.clear()
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status)


@pytest.mark.parametrize(
    "workload,agent", [("waiting", "idle"), ("active", "executing")]
)
def test_not_ready_until_every_subordinate_is_active_and_idle(
    ready_status, workload, agent
):
    ready_status.apps[APPLICATION].units[f"{APPLICATION}/1"].subordinates[
        f"{VAULTLOCKER}/1"
    ] = UnitStatus(
        workload_status=StatusInfo(current=workload),
        juju_status=StatusInfo(current=agent),
    )
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status)


def test_blocked_application_does_not_prevent_recovery(ready_status):
    ready_status.apps[VAULTLOCKER] = replace(
        ready_status.apps[VAULTLOCKER], app_status=StatusInfo(current="blocked")
    )
    assert DeployVaultlockerStep._is_vaultlocker_ready(ready_status)


def test_blocked_selected_host_can_reach_retry_action(ready_status):
    principal = ready_status.apps[APPLICATION].units[f"{APPLICATION}/0"]
    principal.subordinates[f"{VAULTLOCKER}/0"] = UnitStatus(
        workload_status=StatusInfo(current="blocked"),
        juju_status=StatusInfo(current="idle"),
    )
    ready_status.apps[APPLICATION].units[f"{APPLICATION}/0"] = replace(
        principal, workload_status=StatusInfo(current="blocked")
    )
    assert DeployVaultlockerStep._is_vaultlocker_ready(ready_status, {"0"})


def test_other_host_does_not_block_selected_host(ready_status):
    other_host = ready_status.apps[APPLICATION].units[f"{APPLICATION}/1"]
    other_host.subordinates.clear()

    assert DeployVaultlockerStep._is_vaultlocker_ready(ready_status, {"0"})
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status, {"1"})
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status, {"2"})


def test_selected_principal_must_have_idle_agent(ready_status):
    principal = ready_status.apps[APPLICATION].units[f"{APPLICATION}/0"]
    ready_status.apps[APPLICATION].units[f"{APPLICATION}/0"] = replace(
        principal, juju_status=StatusInfo(current="executing")
    )
    assert not DeployVaultlockerStep._is_vaultlocker_ready(ready_status, {"0"})


def test_prompt_reuses_configured_offer(step):
    step.vault_offer_url = None
    with (
        mock.patch(
            "sunbeam.steps.hypervisor.get_vault_kv_offer_url",
            return_value="old.vault-kv",
        ),
        mock.patch("sunbeam.core.questions.PromptQuestion") as question,
    ):
        step.prompt(Console())
    question.assert_not_called()
    assert step.vault_offer_url == "old.vault-kv"


def test_prompt_asks_for_missing_offer(step):
    step.vault_offer_url = None
    with (
        mock.patch(
            "sunbeam.steps.hypervisor.get_vault_kv_offer_url", return_value=None
        ),
        mock.patch("sunbeam.core.questions.PromptQuestion") as question,
    ):
        question.return_value.ask.return_value = "new.vault-kv"
        assert step.has_prompts()
        step.prompt(Console())
    question.return_value.ask.assert_called_once()
    assert step.vault_offer_url == "new.vault-kv"


def test_run_applies_offer_before_waiting(step):
    calls = []

    def apply(context):
        assert step.extra_tfvars["vault-kv-offer-url"] == "vault.vault-kv"
        calls.append("apply")
        return Result(ResultType.COMPLETED)

    step.jhelper._wait.side_effect = lambda *args, **kwargs: calls.append("wait")
    with (
        mock.patch("sunbeam.steps.hypervisor.set_vault_kv_offer_url") as set_offer,
        mock.patch.object(ReapplyHypervisorTerraformPlanStep, "run", side_effect=apply),
    ):
        result = step.run(mock.MagicMock())

    set_offer.assert_called_once_with(step.client, "vault.vault-kv")
    assert calls == ["apply", "wait"]
    step.jhelper._wait.assert_called_once()
    assert result.result_type == ResultType.COMPLETED


def test_run_uses_selected_machine_and_skips_all_hypervisor_wait(step, ready_status):
    step.target_nodes = ["compute-1.example.com"]
    step.client.cluster.get_node_info.return_value = {"machineid": "0"}
    with (
        mock.patch("sunbeam.steps.hypervisor.set_vault_kv_offer_url"),
        mock.patch("sunbeam.steps.hypervisor.get_external_network_configs"),
        mock.patch("sunbeam.steps.hypervisor.get_pci_allowedlist_config"),
        mock.patch("sunbeam.steps.hypervisor.get_dpdk_config"),
    ):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    step.jhelper.wait_until_desired_status.assert_not_called()
    step.client.cluster.get_node_info.assert_called_once_with("compute-1.example.com")
    predicate = step.jhelper._wait.call_args.args[0]
    assert predicate(ready_status)
    assert not predicate(mock.Mock(apps={}))


def test_run_rejects_conflicting_offer_before_apply(step):
    with (
        mock.patch(
            "sunbeam.steps.hypervisor.set_vault_kv_offer_url",
            side_effect=ValueError("different offer"),
        ),
        mock.patch.object(ReapplyHypervisorTerraformPlanStep, "run") as apply,
    ):
        result = step.run(mock.MagicMock())
    assert result.result_type == ResultType.FAILED
    assert result.message == "different offer"
    apply.assert_not_called()
    step.jhelper._wait.assert_not_called()


def test_run_rejects_missing_offer(step):
    step.vault_offer_url = None
    with (
        mock.patch(
            "sunbeam.steps.hypervisor.get_vault_kv_offer_url", return_value=None
        ),
        mock.patch.object(ReapplyHypervisorTerraformPlanStep, "run") as apply,
    ):
        step.prompt(None)
        result = step.run(mock.MagicMock())
    assert result.result_type == ResultType.FAILED
    apply.assert_not_called()


def test_run_does_not_wait_after_apply_failure(step):
    with (
        mock.patch("sunbeam.steps.hypervisor.set_vault_kv_offer_url"),
        mock.patch.object(
            ReapplyHypervisorTerraformPlanStep,
            "run",
            return_value=Result(ResultType.FAILED, "apply failed"),
        ),
    ):
        result = step.run(mock.MagicMock())
    assert result.result_type == ResultType.FAILED
    assert result.message == "apply failed"
    step.jhelper._wait.assert_not_called()


def test_run_reports_readiness_timeout(step):
    step.jhelper._wait.side_effect = TimeoutError("timed out")
    with (
        mock.patch("sunbeam.steps.hypervisor.set_vault_kv_offer_url"),
        mock.patch.object(
            ReapplyHypervisorTerraformPlanStep,
            "run",
            return_value=Result(ResultType.COMPLETED),
        ),
    ):
        result = step.run(mock.MagicMock())
    assert result.result_type == ResultType.FAILED
    assert "Vaultlocker is not ready" in result.message
