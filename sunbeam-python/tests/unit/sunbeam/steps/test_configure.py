# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.steps import configure


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
