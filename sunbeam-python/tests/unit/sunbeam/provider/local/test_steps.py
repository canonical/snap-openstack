# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from unittest import mock
from unittest.mock import Mock, patch

import pytest

import sunbeam.core.questions
import sunbeam.provider.local.steps as local_steps
from sunbeam.core.common import ResultType
from sunbeam.provider.common import nic_utils

from ...steps.test_configure import (
    BaseTestSetHypervisorUnitsOptionsStep,
    BaseTestUserQuestions,
)


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
def fetch_nics():
    with patch.object(nic_utils, "fetch_nics") as p:
        yield p


@pytest.fixture()
def fetch_gpus():
    with patch.object(nic_utils, "fetch_gpus") as p:
        yield p


class TestLocalUserQuestions(BaseTestUserQuestions):
    __test__ = True

    def get_step(self):
        return local_steps.LocalUserQuestions(self.cclient, Path("/tmp/dummy"))

    def test_prompt_local_demo_setup(self):
        self.load_answers.return_value = {}
        self.cclient.cluster.list_nodes.return_value = [{"name": "test-node"}]
        self.cclient.cluster.get_node_info.return_value = {
            "role": ["compute", "control"]
        }

        user_bank_mock, net_bank_mock = self.configure_mocks(self.question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "local"
        user_bank_mock.run_demo_setup.ask.return_value = True

        step = self.get_step()
        with patch(
            "sunbeam.commands.configure.utils.get_fqdn", return_value="test-node"
        ):
            step.prompt()
        self.check_demo_questions(user_bank_mock, net_bank_mock)
        self.check_not_remote_questions(net_bank_mock)

    def test_prompt_local_no_demo_setup(self):
        self.load_answers.return_value = {}
        self.cclient.cluster.list_nodes.return_value = [{"name": "test-node"}]
        self.cclient.cluster.get_node_info.return_value = {
            "role": ["compute", "control"]
        }

        user_bank_mock, net_bank_mock = self.configure_mocks(self.question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "local"
        user_bank_mock.run_demo_setup.ask.return_value = False

        step = self.get_step()
        with patch(
            "sunbeam.commands.configure.utils.get_fqdn", return_value="test-node"
        ):
            step.prompt()
        self.check_not_demo_questions(user_bank_mock, net_bank_mock)
        self.check_not_remote_questions(net_bank_mock)


class TestLocalSetHypervisorUnitsOptionsStep(BaseTestSetHypervisorUnitsOptionsStep):
    __test__ = True

    @pytest.fixture(autouse=True)
    def setup_local(self, fetch_nics):
        self.fetch_nics = fetch_nics

    @pytest.fixture
    def physical_network_question(self):
        with patch("sunbeam.provider.local.steps.physical_network_question") as p:
            yield p

    def get_step(self, join_mode=False):
        return local_steps.LocalSetHypervisorUnitsOptionsStep(
            self.cclient, "maas0.local", self.jhelper, "test-model", join_mode=join_mode
        )

    def mock_physnet_qs(self, physical_network_question):
        physnet_name_mock = Mock()
        physnet_name_mock.ask.return_value = "physnet1"
        configure_more_mock = Mock()
        configure_more_mock.ask.return_value = False

        physnet_qs = {
            "physnet_name": physnet_name_mock,
            "configure_more": configure_more_mock,
        }
        physical_network_question.return_value = physnet_qs

    def test_prompt_remote(self, fetch_nics, physical_network_question):
        self.load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        # Mock no network nodes in cluster
        self.cclient.cluster.list_nodes_by_role.return_value = []
        local_hypervisor_bank_mock = Mock()
        self.question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        local_hypervisor_bank_mock.nics.question = "Select interface"

        self.mock_physnet_qs(physical_network_question)

        step = self.get_step()
        nics_result = {
            "nics": [
                {"name": "eth2", "up": True, "connected": True, "configured": False}
            ],
            "candidates": ["eth2"],
        }
        fetch_nics.return_value = nics_result
        step.prompt()
        assert step.bridge_mappings["maas0.local"] == "br-physnet1:physnet1:eth2"

    def test_prompt_remote_join(self, fetch_nics, physical_network_question):
        self.load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        # Mock no network nodes in cluster
        self.cclient.cluster.list_nodes_by_role.return_value = []
        local_hypervisor_bank_mock = Mock()
        self.question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        local_hypervisor_bank_mock.nics.question = "Select interface"

        self.mock_physnet_qs(physical_network_question)

        step = self.get_step(join_mode=True)
        nics_result = {
            "nics": [
                {"name": "eth2", "up": True, "connected": True, "configured": False}
            ],
            "candidates": ["eth2"],
        }
        fetch_nics.return_value = nics_result
        step.prompt()
        assert step.bridge_mappings["maas0.local"] == "br-physnet1:physnet1:eth2"

    def mock_candidates(self, candidates: list[str]):
        # Construct the return value expected by nic_utils.fetch_nics
        nics_result = {
            "nics": [
                {"name": c, "up": True, "connected": True, "configured": False}
                for c in candidates
            ],
            "candidates": candidates,
        }
        self.fetch_nics.return_value = nics_result

    def test_prompt(self, physical_network_question):
        self.load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        # Mock no network nodes in cluster
        self.cclient.cluster.list_nodes_by_role.return_value = []

        hypervisor_bank_mock = Mock()
        self.question_bank.return_value = hypervisor_bank_mock
        hypervisor_bank_mock.nics.ask.return_value = "eth2"
        hypervisor_bank_mock.nics.question = "Select interface"

        self.mock_physnet_qs(physical_network_question)

        step = self.get_step()

        self.mock_candidates(["eth2"])

        step.prompt()

        machine_name = self.get_machine_name()
        assert step.bridge_mappings[machine_name] == "br-physnet1:physnet1:eth2"

    def test_prompt_local(self):
        """Test specific to Local provider: local access mode."""
        self.load_answers.return_value = {"user": {"remote_access_location": "local"}}
        local_hypervisor_bank_mock = Mock()
        self.question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth12"
        local_hypervisor_bank_mock.nics.question = "Select interface"
        step = self.get_step()
        step.prompt()
        assert len(step.bridge_mappings) == 0

    def test_prompt_local_join(self, physical_network_question):
        """Test specific to Local provider: local access mode with join."""
        self.load_answers.return_value = {"user": {"remote_access_location": "local"}}
        # Mock no network nodes in cluster
        self.cclient.cluster.list_nodes_by_role.return_value = []
        local_hypervisor_bank_mock = Mock()
        self.question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nics.ask.return_value = "eth2"
        local_hypervisor_bank_mock.nics.question = "Select interface"

        self.mock_physnet_qs(physical_network_question)

        step = self.get_step(join_mode=True)
        self.mock_candidates(["eth2"])

        step.prompt()
        assert step.bridge_mappings["maas0.local"] == "br-physnet1:physnet1:eth2"

    def test_prompt_join_mode(self, physical_network_question):
        self.load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        # Mock no network nodes in cluster
        self.cclient.cluster.list_nodes_by_role.return_value = []

        hypervisor_bank_mock = Mock()
        self.question_bank.return_value = hypervisor_bank_mock
        hypervisor_bank_mock.nics.ask.return_value = "eth2"
        hypervisor_bank_mock.nics.question = "Select interface"

        self.mock_physnet_qs(physical_network_question)

        step = self.get_step(join_mode=True)

        self.mock_candidates(["eth2"])

        step.prompt()

        machine_name = self.get_machine_name()
        assert step.bridge_mappings[machine_name] == "br-physnet1:physnet1:eth2"


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
                    {
                        "address": "0000:8a:0.1",
                        "vendor_id": "10de",
                        "product_id": "1db4",
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
                    {
                        "address": "0000:8a:0.1",
                        "vendor_id": "10de",
                        "product_id": "1db4",
                    },
                ],
                # Expected excluded devices
                {
                    "maas0.local": ["0000:2a:0.2"],
                    "other-node": ["0000:0:0.2"],
                },
            ),
            # --accept-defaults was passed, we're still preserving the
            # previous values.
            # Exclude GPU device using manifest
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
                    "maas0.local": ["0000:2a:0.2", "0000:8a:0.1"],
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
                    "maas0.local": ["0000:2a:0.2", "0000:8a:0.1"],
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
        fetch_gpus,
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
        gpu_list = [
            {
                "pci_address": "0000:8a:0.1",
                "vendor_id": "0x10de",
                "product_id": "0x1db4",
            },
        ]
        load_answers.return_value = prev_answers
        fetch_nics.return_value = {
            "nics": nic_list,
            "candidates": [],
        }
        fetch_gpus.return_value = {"gpus": gpu_list}
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
        fetch_gpus,
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
        fetch_gpus.return_value = {"gpus": []}
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
        fetch_gpus,
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
        fetch_gpus.return_value = {"gpus": []}
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
