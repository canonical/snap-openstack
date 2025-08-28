# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest import mock
from unittest.mock import Mock, patch

import pytest

import sunbeam.core.questions
import sunbeam.provider.local.steps as local_steps
import sunbeam.utils
from sunbeam.core.common import ResultType
from sunbeam.provider.common import nic_utils


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
def prompt_question():
    with patch.object(sunbeam.core.questions, "PromptQuestion") as p:
        yield p


@pytest.fixture()
def confirm_question():
    with patch.object(sunbeam.core.questions, "ConfirmQuestion") as p:
        yield p


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def deployment():
    yield Mock()


@pytest.fixture()
def fetch_nics():
    with patch.object(nic_utils, "fetch_nics") as p:
        yield p


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
        fetch_nics,
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
        fetch_nics.return_value = nics_result
        step.prompt()
        assert step.nics["maas0.local"] == "eth2"

    def test_prompt_remote_join(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
        fetch_nics,
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
        fetch_nics.return_value = nics_result
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
        fetch_nics,
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
        fetch_nics.return_value = nics_result
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


class TestLocalConfigSRIOVStep:
    def _get_step(self, manifest=None, accept_defaults=False):
        return local_steps.LocalConfigSRIOVStep(
            mock.Mock(),
            "maas0.local",
            mock.Mock(),
            "test-model",
            manifest=manifest,
            accept_defaults=accept_defaults,
        )

    def test_has_prompts(self):
        assert self._get_step().has_prompts()

    def test_is_skip_should_skip_false(self):
        """Test is_skip returns COMPLETED when should_skip is False."""
        step = self._get_step()
        step.should_skip = False
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_should_skip_true(self):
        """Test is_skip returns SKIPPED when should_skip is True."""
        step = self._get_step()
        step.should_skip = True
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_should_skip_initialization(self):
        """Test that should_skip is initialized to False in constructor."""
        step = self._get_step()
        assert step.should_skip is False

    @pytest.mark.parametrize(
        "prev_answers, accept_defaults, manifest_dev_specs, manifest_excl_devs, "
        "confirm_answers, prompt_answers, exp_dev_specs, exp_excl_devs",
        # For simplicity, the same list of nics will be used for all test cases.
        # It's defined inside the test function.
        [
            # The following scenario merges manifest data with previous answers and
            # the prompt answers.
            (
                # Previous answers from another node
                {
                    "pci_whitelist": [
                        {
                            "vendor_id": "0001",
                            "product_id": "0001",
                            "address": "0000:0:0.1",
                            "physical_network": "physnet1",
                        }
                    ],
                    "excluded_devices": {"other-node": ["0000:0:0.2"]},
                },
                # Accept defaults
                False,
                # Manifest dev specs
                [
                    # Other device, not SR-IOV
                    {
                        "address": {
                            "domain": ".*",
                            "bus": "1b",
                            "slot": "10",
                            "function": "[0-4]",
                        }
                    },
                    {"address": ":2a:", "physical_network": "physnet2"},
                ],
                # Manifest excluded devices
                {
                    "maas0.local": ["0000:2a:0.2"],
                },
                # Whitelist confirmation answers,
                [True, False, True, True, True, True],
                # Physnet prompt answers,
                ["physnet1", "physnet2", "physnet2", "physnet3", ""],
                # Expected device specs
                [
                    {
                        "address": {
                            "domain": ".*",
                            "bus": "1b",
                            "slot": "10",
                            "function": "[0-4]",
                        }
                    },
                    {"address": ":2a:", "physical_network": "physnet2"},
                    {
                        "vendor_id": "0001",
                        "product_id": "0001",
                        "address": "0000:0:0.1",
                        "physical_network": "physnet1",
                    },
                    {
                        "vendor_id": "0003",
                        "product_id": "0003",
                        "address": "0000:3a:0.1",
                        "physical_network": "physnet3",
                    },
                    {
                        "vendor_id": "0003",
                        "product_id": "0003",
                        "address": "0000:3a:0.2",
                        "physical_network": None,
                    },
                ],
                # Expected excluded devices
                {
                    "other-node": ["0000:0:0.2"],
                    "maas0.local": ["0000:0:0.2"],
                },
            ),
            # --accept-defaults was passed, we're still preserving the
            # previous values.
            (
                # Previous answers
                {
                    "pci_whitelist": [
                        {
                            "vendor_id": "0001",
                            "product_id": "0001",
                            "address": "0000:0:0.1",
                            "physical_network": "physnet1",
                        }
                    ],
                    "excluded_devices": {"other-node": ["0000:0:0.2"]},
                },
                # Accept defaults
                True,
                # Manifest dev specs
                [
                    # Other device, not SR-IOV
                    {
                        "address": {
                            "domain": ".*",
                            "bus": "1b",
                            "slot": "10",
                            "function": "[0-4]",
                        }
                    },
                    {"address": ":2a:", "physical_network": "physnet2"},
                ],
                # Manifest excluded devices
                {
                    "maas0.local": ["0000:2a:0.2"],
                },
                # Whitelist confirmation answers,
                [True, False, True, True, True, False],
                # Physnet prompt answers,
                ["physnet1", "none", "physnet2", "physnet2", "physnet3"],
                # Expected device specs
                [
                    {
                        "address": {
                            "domain": ".*",
                            "bus": "1b",
                            "slot": "10",
                            "function": "[0-4]",
                        }
                    },
                    {"address": ":2a:", "physical_network": "physnet2"},
                    {
                        "vendor_id": "0001",
                        "product_id": "0001",
                        "address": "0000:0:0.1",
                        "physical_network": "physnet1",
                    },
                ],
                # Expected excluded devices
                {
                    "maas0.local": ["0000:2a:0.2"],
                    "other-node": ["0000:0:0.2"],
                },
            ),
        ],
    )
    def test_prompt(
        self,
        load_answers,
        write_answers,
        prompt_question,
        confirm_question,
        question_bank,
        fetch_nics,
        prev_answers,
        accept_defaults,
        manifest_dev_specs,
        manifest_excl_devs,
        confirm_answers,
        prompt_answers,
        exp_dev_specs,
        exp_excl_devs,
    ):
        nic_list = [
            {
                "pci_address": "0000:0:0.1",
                "vendor_id": "0x0001",
                "product_id": "0x0001",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno1",
            },
            {
                "pci_address": "0000:0:0.2",
                "vendor_id": "0x0001",
                "product_id": "0x0001",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno2",
            },
            {
                "pci_address": "0000:2a:0.1",
                "vendor_id": "0x0002",
                "product_id": "0x0002",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno3",
            },
            {
                "pci_address": "0000:2a:0.2",
                "vendor_id": "0x0002",
                "product_id": "0x0002",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno4",
            },
            # SR-IOV unavailable, shouldn't prompt
            {
                "pci_address": "0000:11:11.2",
                "vendor_id": "0x0005",
                "product_id": "0x0005",
                "pf_pci_address": "",
                "sriov_available": False,
                "name": "eno5",
            },
            {
                "pci_address": "0000:3a:0.1",
                "vendor_id": "0x0003",
                "product_id": "0x0003",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno6",
            },
            {
                "pci_address": "0000:3a:0.2",
                "vendor_id": "0x0003",
                "product_id": "0x0003",
                "pf_pci_address": "",
                "sriov_available": True,
                "name": "eno7",
            },
        ]
        load_answers.return_value = prev_answers
        fetch_nics.return_value = {
            "nics": nic_list,
            "candidates": [],
        }
        sriov_question = question_bank.return_value.configure_sriov
        sriov_question.ask.return_value = True
        confirm_question.return_value.ask.side_effect = confirm_answers
        prompt_question.return_value.ask.side_effect = prompt_answers

        if manifest_dev_specs or manifest_excl_devs:
            manifest = mock.Mock()
            manifest.core.config.pci.device_specs = manifest_dev_specs or []
            manifest.core.config.pci.excluded_devices = manifest_excl_devs or []
        else:
            manifest = None

        step = self._get_step(manifest, accept_defaults)
        step.prompt(mock.sentinel.console)

        if accept_defaults:
            sriov_question.ask.assert_not_called()

        assert exp_dev_specs == step.variables["pci_whitelist"]
        assert exp_excl_devs == step.variables["excluded_devices"]

        write_answers.assert_called_once_with(step.client, "PCI", step.variables)

    def test_prompt_no_sriov_devices_sets_should_skip(
        self,
        load_answers,
        write_answers,
        question_bank,
        fetch_nics,
    ):
        """Test that should_skip is set to True when no SR-IOV devices are detected."""
        # Mock no SR-IOV devices available
        nic_list = [
            {
                "pci_address": "0000:0:0.1",
                "vendor_id": "0x0001",
                "product_id": "0x0001",
                "pf_pci_address": "",
                "sriov_available": False,  # No SR-IOV available
                "name": "eno1",
            },
        ]

        load_answers.return_value = {}
        fetch_nics.return_value = {
            "nics": nic_list,
            "candidates": [],
        }
        sriov_question = question_bank.return_value.configure_sriov
        sriov_question.ask.return_value = True

        step = self._get_step()
        step.prompt(mock.sentinel.console)

        # should_skip should be set to True when no SR-IOV devices are found
        assert step.should_skip is True

    def test_prompt_with_sriov_devices_does_not_set_should_skip(
        self,
        load_answers,
        write_answers,
        question_bank,
        fetch_nics,
        confirm_question,
        prompt_question,
    ):
        """Test that should_skip remains False when SR-IOV devices are detected."""
        # Mock SR-IOV devices available
        nic_list = [
            {
                "pci_address": "0000:0:0.1",
                "vendor_id": "0x0001",
                "product_id": "0x0001",
                "pf_pci_address": "",
                "sriov_available": True,  # SR-IOV available
                "name": "eno1",
            },
        ]

        load_answers.return_value = {}
        fetch_nics.return_value = {
            "nics": nic_list,
            "candidates": [],
        }
        sriov_question = question_bank.return_value.configure_sriov
        sriov_question.ask.return_value = True
        confirm_question.return_value.ask.return_value = (
            False  # Don't whitelist any devices
        )
        prompt_question.return_value.ask.return_value = "physnet1"

        step = self._get_step()
        step.prompt(mock.sentinel.console)

        # should_skip should remain False when SR-IOV devices are found
        assert step.should_skip is False
