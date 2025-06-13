# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

import sunbeam.core.questions
import sunbeam.provider.local.steps as local_steps
import sunbeam.utils
from sunbeam.core.common import ResultType


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.core.questions, "load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch.object(sunbeam.core.questions, "write_answers") as p:
        yield p


@pytest.fixture()
def question_bank():
    with patch.object(sunbeam.core.questions, "QuestionBank") as p:
        yield p


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def deployment():
    yield Mock()


class TestLocalSetHypervisorUnitsOptionsStep:
    def test_has_prompts(self, cclient, jhelper):
        step = local_steps.LocalSetHypervisorUnitsOptionsStep(
            cclient, "maas0.local", jhelper, "test-model"
        )
        assert step.has_prompts()

    def test_prompt_remote(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
    ):
        load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        step = local_steps.LocalSetHypervisorUnitsOptionsStep(
            cclient, "maas0.local", jhelper, "test-model"
        )
        nics_result = {
            "nics": [
                {"name": "eth2", "up": True, "connected": True, "configured": False}
            ],
            "candidates": ["eth2"],
        }
        step._fetch_nics = Mock(return_value=nics_result)
        step.prompt()
        assert step.nics["maas0.local"] == "eth2"

    def test_prompt_remote_join(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
    ):
        load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        step = local_steps.LocalSetHypervisorUnitsOptionsStep(
            cclient, "maas0.local", jhelper, "test-model", join_mode=True
        )
        nics_result = {
            "nics": [
                {"name": "eth2", "up": True, "connected": True, "configured": False}
            ],
            "candidates": ["eth2"],
        }
        step._fetch_nics = Mock(return_value=nics_result)
        step.prompt()
        assert step.nics["maas0.local"] == "eth2"

    def test_prompt_local(self, cclient, jhelper, load_answers, question_bank):
        load_answers.return_value = {"user": {"remote_access_location": "local"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth12"
        step = local_steps.LocalSetHypervisorUnitsOptionsStep(
            cclient, "maas0.local", jhelper, "tes-model"
        )
        step.prompt()
        assert len(step.nics) == 0

    def test_prompt_local_join(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
    ):
        load_answers.return_value = {"user": {"remote_access_location": "local"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        step = local_steps.LocalSetHypervisorUnitsOptionsStep(
            cclient, "maas0.local", jhelper, "test-model", join_mode=True
        )
        nics_result = {
            "nics": [
                {"name": "eth2", "up": True, "connected": True, "configured": False}
            ],
            "candidates": ["eth2"],
        }
        step._fetch_nics = Mock(return_value=nics_result)
        step.prompt()
        assert step.nics["maas0.local"] == "eth2"


class TestLocalClusterStatusStep:
    def test_run(self, deployment, jhelper):
        jhelper.get_model_status.return_value = Mock(machines={}, apps={})
        deployment.get_client().cluster.get_status.return_value = {
            "node-1": {"status": "ONLINE", "address": "10.0.0.1"}
        }

        step = local_steps.LocalClusterStatusStep(deployment, jhelper)
        result = step.run(Mock())
        assert result.result_type == ResultType.COMPLETED

    def test_compute_status(self, deployment, jhelper):
        model = "test-model"
        hostname = "node-1"
        host_ip = "10.0.0.1"

        deployment.get_client().cluster.get_status.return_value = {
            hostname: {"status": "ONLINE", "address": f"{host_ip}:7000"}
        }
        deployment.openstack_machines_model = model
        jhelper.get_model_status.return_value = Mock(
            machines={
                "0": Mock(
                    hostname=hostname,
                    dns_name=host_ip,
                    machine_status=Mock(current="running"),
                )
            },
            apps={
                "k8s": Mock(
                    units={
                        "k8s/0": Mock(
                            machine="0",
                            workload_status=Mock(current="active"),
                        )
                    }
                )
            },
        )
        expected_status = {
            model: {
                "0": {
                    "hostname": hostname,
                    "status": {
                        "cluster": "ONLINE",
                        "machine": "running",
                        "control": "active",
                    },
                }
            }
        }

        step = local_steps.LocalClusterStatusStep(deployment, jhelper)
        actual_status = step._compute_status()

        assert expected_status == actual_status

    def test_compute_status_with_missing_hostname_in_model_status(
        self, deployment, jhelper
    ):
        model = "test-model"
        hostname = "node-1"
        host_ip = "10.0.0.1"

        deployment.get_client().cluster.get_status.return_value = {
            hostname: {"status": "ONLINE", "address": f"{host_ip}:7000"}
        }
        deployment.openstack_machines_model = model
        # missing hostname attribute in model status
        jhelper.get_model_status.return_value = Mock(
            machines={
                "0": Mock(
                    hostname=None,
                    dns_name=host_ip,
                    machine_status=Mock(current="running"),
                )
            },
            apps={
                "k8s": Mock(
                    units={
                        "k8s/0": Mock(
                            machine="0",
                            workload_status=Mock(current="active"),
                        )
                    }
                )
            },
        )

        expected_status = {
            model: {
                "0": {
                    "hostname": hostname,
                    "status": {
                        "cluster": "ONLINE",
                        "machine": "running",
                        "control": "active",
                    },
                }
            }
        }

        step = local_steps.LocalClusterStatusStep(deployment, jhelper)
        actual_status = step._compute_status()

        assert expected_status == actual_status
