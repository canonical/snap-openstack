# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from rich.console import Console

import sunbeam.commands.configure as configure
import sunbeam.jobs.questions
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.configure.run_sync", run_sync)
    yield
    loop.close()


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.jobs.questions, "load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch.object(sunbeam.jobs.questions, "write_answers") as p:
        yield p


@pytest.fixture()
def question_bank():
    with patch.object(sunbeam.jobs.questions, "QuestionBank") as p:
        yield p


@pytest.fixture()
def jhelper():
    yield AsyncMock()


@pytest.fixture()
def thelper():
    yield Mock(path=Path())


@pytest.fixture()
def get_nic_macs():
    with patch.object(sunbeam.utils, "get_nic_macs") as p:
        p.return_value = ["00:16:3e:01:6e:75"]
        yield p


@pytest.fixture()
def get_free_nics():
    with patch.object(sunbeam.utils, "get_free_nics") as p:
        p.return_value = ["eth1", "eth2"]
        yield p


@pytest.fixture()
def is_nic_up():
    with patch.object(sunbeam.utils, "is_nic_up") as p:
        p.side_effect = lambda x: {"eth1": True, "eth2": True}[x]
        yield p


@pytest.fixture()
def is_configured():
    with patch.object(sunbeam.utils, "is_configured") as p:
        p.side_effect = lambda x: {"eth1": False, "eth2": False}[x]
        yield p


@pytest.fixture()
def is_nic_connected():
    with patch.object(sunbeam.utils, "is_nic_connected") as p:
        p.side_effect = lambda x: {"eth1": True, "eth2": True}[x]
        yield p


class SetHypervisorCharmConfigStep:
    def test_is_skip(self, cclient, jhelper):
        step = configure.SetHypervisorCharmConfigStep(
            cclient, jhelper, "/tmp/dummypath"
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_remote_access(self, load_answers, jhelper, cclient):
        load_answers.return_value = {
            "user": {"remote_access_location": "remote"},
            "external_network": {"physical_network": "physnet1"},
        }
        step = configure.SetHypervisorCharmConfigStep(
            cclient, jhelper, "/tmp/dummypath"
        )
        step.run()
        jhelper.set_application_config.assert_called_once_with(
            "controller",
            "openstack-hypervisor",
            {
                "enable-gateway": "True",
                "external-bridge": "br-ex",
                "external-bridge-address": "0.0.0.0/0",
                "physnet-name": "physnet1",
            },
        )

    def test_run_remote_access_local(self, load_answers, jhelper, cclient):
        load_answers.return_value = {
            "user": {"remote_access_location": "local"},
            "external_network": {
                "gateway": "10.0.0.1",
                "cidr": "10.0.0.0/16",
                "physical_network": "physnet1",
            },
        }
        step = configure.SetHypervisorCharmConfigStep(
            cclient, jhelper, "/tmp/dummypath"
        )
        step.run()
        jhelper.set_application_config.assert_called_once_with(
            "controller",
            "openstack-hypervisor",
            {
                "enable-gateway": "False",
                "external-bridge": "br-ex",
                "external-bridge-address": "10.0.0.1/16",
                "physnet-name": "physnet1",
            },
        )


class TestUserQuestions:
    def test_has_prompts(self, cclient):
        step = configure.UserQuestions(cclient, jhelper)
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
        bank_mocks = [net_bank_mock, user_bank_mock]
        question_bank.side_effect = lambda *args, **kwargs: bank_mocks.pop()
        self.set_net_common_answers(net_bank_mock)
        return user_bank_mock, net_bank_mock

    def test_prompt_remote_demo_setup(
        self, cclient, load_answers, question_bank, write_answers, get_nic_macs
    ):
        load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "remote"
        user_bank_mock.run_demo_setup.ask.return_value = True
        step = configure.UserQuestions(cclient, jhelper)
        step.prompt()
        self.check_demo_questions(user_bank_mock, net_bank_mock)
        self.check_remote_questions(net_bank_mock)

    def test_prompt_remote_no_demo_setup(
        self, cclient, load_answers, question_bank, write_answers, get_nic_macs
    ):
        load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "remote"
        user_bank_mock.run_demo_setup.ask.return_value = False
        step = configure.UserQuestions(cclient, jhelper)
        step.prompt()
        self.check_not_demo_questions(user_bank_mock, net_bank_mock)
        self.check_remote_questions(net_bank_mock)

    def test_prompt_local_demo_setup(
        self, cclient, load_answers, question_bank, write_answers
    ):
        load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "local"
        user_bank_mock.run_demo_setup.ask.return_value = True
        step = configure.UserQuestions(cclient, jhelper)
        step.prompt()
        self.check_demo_questions(user_bank_mock, net_bank_mock)
        self.check_not_remote_questions(net_bank_mock)

    def test_prompt_local_no_demo_setup(
        self, cclient, load_answers, question_bank, write_answers
    ):
        load_answers.return_value = {}
        user_bank_mock, net_bank_mock = self.configure_mocks(question_bank)
        user_bank_mock.remote_access_location.ask.return_value = "local"
        user_bank_mock.run_demo_setup.ask.return_value = False
        step = configure.UserQuestions(cclient, jhelper)
        step.prompt()
        self.check_not_demo_questions(user_bank_mock, net_bank_mock)
        self.check_not_remote_questions(net_bank_mock)


class TestUserOpenRCStep:
    def test_is_skip_with_demo(self, tmpdir, cclient, load_answers):
        outfile = tmpdir + "/" + "openrc"
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.UserOpenRCStep(cclient, "http://keystone:5000", "3", outfile)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, tmpdir, cclient, load_answers):
        outfile = tmpdir + "/" + "openrc"
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.UserOpenRCStep(cclient, "http://keystone:5000", "3", outfile)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, mocker, tmpdir, cclient, run, snap, environ):
        mocker.patch.object(configure, "Snap", return_value=snap)
        environ.copy.return_value = {}
        outfile = tmpdir + "/" + "openrc"
        runout_mock = Mock()
        creds = {
            "OS_USERNAME": {"value": "user1"},
            "OS_PASSWORD": {"value": "reallyhardpassword"},
            "OS_USER_DOMAIN_NAME": {"value": "userdomain"},
            "OS_PROJECT_DOMAIN_NAME": {"value": "projectdomain"},
            "OS_PROJECT_NAME": {"value": "projectname"},
        }
        runout_mock.stdout = json.dumps(creds)
        runout_mock.sterr = ""
        run.return_value = runout_mock
        auth_url = "http://keystone:5000"
        auth_version = 3
        step = configure.UserOpenRCStep(cclient, auth_url, "3", outfile)
        step.run()
        with open(outfile, "r") as f:
            contents = f.read()
        expect = f"""# openrc for {creds["OS_USERNAME"]["value"]}
export OS_AUTH_URL={auth_url}
export OS_USERNAME={creds["OS_USERNAME"]["value"]}
export OS_PASSWORD={creds["OS_PASSWORD"]["value"]}
export OS_USER_DOMAIN_NAME={creds["OS_USER_DOMAIN_NAME"]["value"]}
export OS_PROJECT_DOMAIN_NAME={creds["OS_PROJECT_DOMAIN_NAME"]["value"]}
export OS_PROJECT_NAME={creds["OS_PROJECT_NAME"]["value"]}
export OS_AUTH_VERSION={auth_version}
export OS_IDENTITY_API_VERSION={auth_version}"""
        assert contents == expect


class TestDemoSetup:
    def test_is_skip_demo_setup(self, cclient, thelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.DemoSetup(cclient, thelper, "/tmp/dummy")
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, cclient, thelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.DemoSetup(cclient, thelper, "/tmp/dummy")
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, thelper, load_answers):
        answer_data = {"user": {"foo": "bar"}}
        load_answers.return_value = answer_data
        step = configure.DemoSetup(cclient, thelper, "/tmp/dummy")
        result = step.run()
        thelper.write_tfvars.assert_called_once_with(answer_data, "/tmp/dummy")
        assert result.result_type == ResultType.COMPLETED

    def test_run_fail(self, cclient, thelper, load_answers):
        answer_data = {"user": {"foo": "bar"}}
        load_answers.return_value = answer_data
        thelper.apply.side_effect = TerraformException("Bad terraform")
        step = configure.DemoSetup(cclient, thelper, "/tmp/dummy")
        result = step.run()
        assert result.result_type == ResultType.FAILED


class TestTerraformDemoInitStep:
    def test_is_skip_demo_setup(self, cclient, thelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.TerraformDemoInitStep(cclient, thelper)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, cclient, thelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.TerraformDemoInitStep(cclient, thelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED


class TestSetLocalHypervisorOptions:
    def test_has_prompts(self, cclient, jhelper):
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        assert step.has_prompts()

    def test_prompt_remote(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
        get_nic_macs,
        get_free_nics,
        is_configured,
        is_nic_up,
        is_nic_connected,
    ):
        get_free_nics.return_value = ["eth2"]
        load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nic.ask.return_value = "eth2"
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        step.prompt()
        assert step.nic == "eth2"

    def test_prompt_remote_join(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
        get_nic_macs,
        get_free_nics,
        is_configured,
        is_nic_up,
        is_nic_connected,
    ):
        load_answers.return_value = {"user": {"remote_access_location": "remote"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nic.ask.return_value = "eth2"
        step = configure.SetLocalHypervisorOptions(
            cclient, "maas0.local", jhelper, join_mode=True
        )
        step.prompt()
        assert step.nic == "eth2"

    def test_prompt_local(self, cclient, jhelper, load_answers, question_bank):
        load_answers.return_value = {"user": {"remote_access_location": "local"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nic.ask.return_value = "eth12"
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        step.prompt()
        assert step.nic is None

    def test_prompt_local_join(
        self,
        cclient,
        jhelper,
        load_answers,
        question_bank,
        get_nic_macs,
        get_free_nics,
        is_configured,
        is_nic_up,
        is_nic_connected,
    ):
        load_answers.return_value = {"user": {"remote_access_location": "local"}}
        local_hypervisor_bank_mock = Mock()
        question_bank.return_value = local_hypervisor_bank_mock
        local_hypervisor_bank_mock.nic.ask.return_value = "eth2"
        step = configure.SetLocalHypervisorOptions(
            cclient, "maas0.local", jhelper, join_mode=True
        )
        step.prompt()
        assert step.nic == "eth2"

    def test_run(self, cclient, jhelper):
        jhelper.run_action.return_value = {"return-code": 0}
        unit_mock = Mock()
        unit_mock.entity_id = "openstack-hypervisor/0"
        jhelper.get_unit_from_machine.return_value = unit_mock
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        step.nic = "eth11"
        result = step.run()
        jhelper.run_action.assert_called_once_with(
            "openstack-hypervisor/0",
            "controller",
            "set-hypervisor-local-settings",
            action_params={"external-nic": "eth11"},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_fail(self, cclient, jhelper):
        jhelper.run_action.return_value = {"return-code": 2}
        jhelper.get_leader_unit.return_value = "openstack-hypervisor/0"
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        step.nic = "eth11"
        result = step.run()
        assert result.result_type == ResultType.FAILED

    def test_run_skipped(self, cclient, jhelper):
        step = configure.SetLocalHypervisorOptions(cclient, "maas0.local", jhelper)
        step.nic = None
        step.run()
        assert not jhelper.run_action.called


class TestNicPrompt:
    short_question = "Short Question [eth1/eth2] (eth1): "

    def test_good_choice(
        self, get_free_nics, is_nic_up, is_configured, is_nic_connected
    ):
        console = Console(file=io.StringIO())
        INPUT = "eth1\n"
        name = configure.NicPrompt.ask(
            "Short Question",
            console=console,
            stream=io.StringIO(INPUT),
        )
        assert name == "eth1"
        output = console.file.getvalue()
        print(repr(output))
        assert output == self.short_question

    def test_good_choice_default(
        self, get_free_nics, is_nic_up, is_configured, is_nic_connected
    ):
        console = Console(file=io.StringIO())
        INPUT = ""
        name = configure.NicPrompt.ask(
            "Short Question",
            default="eth2",
            console=console,
            stream=io.StringIO(INPUT),
        )
        assert name == "eth2"
        output = console.file.getvalue()
        print(repr(output))
        expected = "Short Question [eth1/eth2] (eth2): "
        assert output == expected

    def test_default_missing_from_machine(
        self, get_free_nics, is_nic_up, is_configured, is_nic_connected
    ):
        console = Console(file=io.StringIO())
        INPUT = ""
        name = configure.NicPrompt.ask(
            "Short Question",
            default="eth3",
            console=console,
            stream=io.StringIO(INPUT),
        )
        # The default eth3 does not exist so it was discarded.
        assert name == "eth1"
        output = console.file.getvalue()
        print(repr(output))
        assert output == self.short_question
