# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ActionFailedException
from sunbeam.steps import configure
from sunbeam.steps.configure import SetExternalNetworkUnitsOptionsStep


class BaseTestUserQuestions:
    __test__ = False

    @pytest.fixture(autouse=True)
    def setup(self, cclient, jhelper, load_answers, question_bank, write_answers):
        self.cclient = cclient
        self.jhelper = jhelper
        self.load_answers = load_answers
        self.question_bank = question_bank
        self.write_answers = write_answers

    def get_step(self) -> configure.BaseUserQuestions:
        raise NotImplementedError

    def setup_remote_access(self, user_bank_mock):
        """Configure environment/mocks to ensure remote access is selected."""
        pass

    def test_has_prompts(self):
        step = self.get_step()
        assert step.has_prompts()

    def check_common_questions(self, bank_mock):
        assert bank_mock.username.ask.called

    def check_demo_questions(self, user_bank_mock, net_bank_mock):
        assert user_bank_mock.username.ask.called
        assert user_bank_mock.password.ask.called
        assert user_bank_mock.cidr.ask.called
        assert user_bank_mock.security_group_rules.ask.called

    def check_not_demo_questions(self, user_bank_mock, net_bank_mock):
        assert not user_bank_mock.username.ask.called
        assert not user_bank_mock.password.ask.called
        assert not user_bank_mock.cidr.ask.called
        assert not user_bank_mock.security_group_rules.ask.called

    def check_remote_questions(self, net_bank_mock):
        assert net_bank_mock.gateway.ask.called

    def check_not_remote_questions(self, net_bank_mock):
        assert not net_bank_mock.gateway.ask.called

    def set_net_common_answers(self, net_bank_mock):
        net_bank_mock.network_type.ask.return_value = "vlan"
        net_bank_mock.cidr.ask.return_value = "10.0.0.0/24"

    def configure_mocks(self, question_bank):
        user_bank_mock = Mock()
        net_bank_mock = Mock()
        physnet_bank_mock = Mock()
        physnet_bank_mock.configure_more.ask.return_value = False
        physnet_bank_mock.physnet_name.ask.return_value = "physnet1"

        # Order of calls: User, Physnet, ExtNet
        # Stack is popped: last in, first out.
        bank_mocks = [net_bank_mock, physnet_bank_mock, user_bank_mock]
        question_bank.side_effect = lambda *args, **kwargs: bank_mocks.pop()
        self.set_net_common_answers(net_bank_mock)
        return user_bank_mock, net_bank_mock

    def test_prompt_remote_demo_setup(self):
        self.load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(self.question_bank)

        self.setup_remote_access(user_bank_mock)
        user_bank_mock.run_demo_setup.ask.return_value = True

        step = self.get_step()
        step.prompt()

        self.check_demo_questions(user_bank_mock, net_bank_mock)
        self.check_remote_questions(net_bank_mock)

    def test_prompt_remote_no_demo_setup(self):
        self.load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(self.question_bank)

        self.setup_remote_access(user_bank_mock)
        user_bank_mock.run_demo_setup.ask.return_value = False

        step = self.get_step()
        step.prompt()

        self.check_not_demo_questions(user_bank_mock, net_bank_mock)
        self.check_remote_questions(net_bank_mock)


class BaseTestSetHypervisorUnitsOptionsStep:
    __test__ = False

    @pytest.fixture(autouse=True)
    def setup(self, cclient, jhelper, load_answers, question_bank):
        self.cclient = cclient
        self.jhelper = jhelper
        self.load_answers = load_answers
        self.question_bank = question_bank

    def get_step(self, join_mode=False):
        raise NotImplementedError

    def get_machine_name(self):
        """Return the expected machine name key in bridge_mappings."""
        return "maas0.local"

    def mock_candidates(self, candidates: list[str]):
        """Mock the backend to return these candidate NICs."""
        raise NotImplementedError

    def test_has_prompts(self):
        step = self.get_step()
        assert step.has_prompts()


class _TestableStep(SetExternalNetworkUnitsOptionsStep):
    APP = "openstack-network-agents"
    DISPLAY_NAME = "OpenStack Network Agents"
    ACTION = "set-network-agents-local-settings"

    def get_unit(self, name: str) -> str:
        raise NotImplementedError


class TestSetExternalNetworkUnitsOptionsStepRun:
    """Tests for SetExternalNetworkUnitsOptionsStep.run(step_context)."""

    def _make_step(self, cclient, jhelper, names):
        step = _TestableStep(
            client=cclient,
            names=names,
            jhelper=jhelper,
            model="openstack",
            manifest=MagicMock(),
        )
        step.get_unit = MagicMock(return_value="openstack-network-agents/0")
        return step

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=False)
    def test_split_roles_off_with_bridge_mapping(
        self, mock_split, cclient, jhelper, step_context
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 1,
            "role": ["network"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={"bridge-mapping": "br-physnet1:physnet1:eth2"},
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=False)
    def test_split_roles_off_no_bridge_mapping_skipped(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 1,
            "role": ["network"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_not_called()

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_network_node_with_bridge(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 1,
            "role": ["network"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={
                "bridge-mapping": "br-physnet1:physnet1:eth2",
                "enable-chassis-as-gw": True,
            },
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_only_with_bridge(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        """Compute-only: bridge_mapping ignored, only gw=false."""
        cclient.cluster.get_node_info.return_value = {
            "machineid": 2,
            "role": ["compute"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={
                "enable-chassis-as-gw": False,
            },
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_only_no_bridge(
        self, mock_split, cclient, jhelper, step_context
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 3,
            "role": ["compute"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={"enable-chassis-as-gw": False},
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_control_only_no_bridge(
        self, mock_split, cclient, jhelper, step_context
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 4,
            "role": ["control"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={"enable-chassis-as-gw": False},
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_control_only_with_bridge_stripped(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        """Control-only node: bridge_mapping ignored even if present."""
        cclient.cluster.get_node_info.return_value = {
            "machineid": 4,
            "role": ["control"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={"enable-chassis-as-gw": False},
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_network_with_bridge(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 5,
            "role": ["compute", "network"],
        }
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
            action_params={
                "bridge-mapping": "br-physnet1:physnet1:eth2",
                "enable-chassis-as-gw": True,
            },
        )

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_on_multiple_mixed_nodes(
        self, mock_split, cclient, jhelper, step_context
    ):
        node_info = {
            "net-node": {"machineid": 1, "role": ["network"]},
            "compute-node": {"machineid": 2, "role": ["compute"]},
            "both-node": {"machineid": 3, "role": ["compute", "network"]},
        }
        cclient.cluster.get_node_info.side_effect = lambda n: node_info[n]

        units = {
            "net-node": "openstack-network-agents/0",
            "compute-node": "openstack-network-agents/1",
            "both-node": "openstack-network-agents/2",
        }

        step = self._make_step(
            cclient, jhelper, ["net-node", "compute-node", "both-node"]
        )
        step.get_unit = MagicMock(side_effect=lambda n: units[n])
        step.bridge_mappings = {
            "net-node": "br-physnet1:physnet1:eth2",
            "both-node": "br-physnet2:physnet2:eth3",
        }

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_count == 3
        calls = jhelper.run_action.call_args_list

        assert calls[0].args == (
            "openstack-network-agents/0",
            "openstack",
            "set-network-agents-local-settings",
        )
        assert calls[0].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet1:physnet1:eth2",
                "enable-chassis-as-gw": True,
            }
        }

        assert calls[1].args == (
            "openstack-network-agents/1",
            "openstack",
            "set-network-agents-local-settings",
        )
        assert calls[1].kwargs == {"action_params": {"enable-chassis-as-gw": False}}

        assert calls[2].args == (
            "openstack-network-agents/2",
            "openstack",
            "set-network-agents-local-settings",
        )
        assert calls[2].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet2:physnet2:eth3",
                "enable-chassis-as-gw": True,
            }
        }

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_action_fails_returns_failed(
        self, mock_split, cclient, jhelper, step_context
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": 1,
            "role": ["network"],
        }
        jhelper.run_action.side_effect = ActionFailedException("boom")
        step = self._make_step(cclient, jhelper, ["node1"])
        step.bridge_mappings = {"node1": "br-physnet1:physnet1:eth2"}

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=True)
    def test_split_roles_multi_physnet_different_hosts(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        """Two network nodes each with a different physnet."""
        node_info = {
            "net-a": {"machineid": 1, "role": ["network"]},
            "net-b": {"machineid": 2, "role": ["network"]},
        }
        cclient.cluster.get_node_info.side_effect = lambda n: node_info[n]
        units = {
            "net-a": "openstack-network-agents/0",
            "net-b": "openstack-network-agents/1",
        }
        step = self._make_step(cclient, jhelper, ["net-a", "net-b"])
        step.get_unit = MagicMock(side_effect=lambda n: units[n])
        step.bridge_mappings = {
            "net-a": "br-physnet1:physnet1:eth2",
            "net-b": "br-physnet2:physnet2:eth3",
        }

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_count == 2
        calls = jhelper.run_action.call_args_list
        assert calls[0].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet1:physnet1:eth2",
                "enable-chassis-as-gw": True,
            }
        }
        assert calls[1].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet2:physnet2:eth3",
                "enable-chassis-as-gw": True,
            }
        }

    @patch("sunbeam.feature_gates.split_roles_enabled", return_value=False)
    def test_no_split_roles_multi_physnet_different_hosts(
        self,
        mock_split,
        cclient,
        jhelper,
        step_context,
    ):
        """Without split-roles, different physnets still work."""
        node_info = {
            "node-a": {"machineid": 1, "role": ["compute", "network"]},
            "node-b": {"machineid": 2, "role": ["compute", "network"]},
        }
        cclient.cluster.get_node_info.side_effect = lambda n: node_info[n]
        units = {
            "node-a": "openstack-network-agents/0",
            "node-b": "openstack-network-agents/1",
        }
        step = self._make_step(cclient, jhelper, ["node-a", "node-b"])
        step.get_unit = MagicMock(side_effect=lambda n: units[n])
        step.bridge_mappings = {
            "node-a": "br-physnet1:physnet1:eth2",
            "node-b": "br-physnet2:physnet2:eth3",
        }

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_count == 2
        calls = jhelper.run_action.call_args_list
        assert calls[0].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet1:physnet1:eth2",
            }
        }
        assert calls[1].kwargs == {
            "action_params": {
                "bridge-mapping": "br-physnet2:physnet2:eth3",
            }
        }
