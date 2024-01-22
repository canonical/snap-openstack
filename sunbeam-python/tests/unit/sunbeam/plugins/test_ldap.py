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
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import TimeoutException
from sunbeam.plugins.ldap.plugin import (
    AddLDAPDomainStep,
    DisableLDAPDomainStep,
    LDAPPlugin,
    UpdateLDAPDomainStep,
)


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def ccclient():
    with patch("sunbeam.plugins.interface.v1.base.Client") as p:
        yield p


@pytest.fixture()
def read_config():
    with patch("sunbeam.plugins.ldap.plugin.read_config") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.plugins.ldap.plugin.update_config") as p:
        yield p


@pytest.fixture()
def ssnap():
    with patch("sunbeam.clusterd.service.Snap") as p:
        yield p


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.plugins.pro.plugin.run_sync", run_sync)
    yield
    loop.close()


class FakeLDAPPlugin(LDAPPlugin):
    def __init__(self):
        self.config_flags = None
        self.name = "ldap"
        self.app_name = self.name.capitalize()
        self.tf_plan_location = 1


class TestAddLDAPDomainStep:
    def setup_method(self):
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.charm_config = {"domain-name": "dom1"}

    def test_is_skip(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = AddLDAPDomainStep(cclient, self.tfhelper, self.jhelper, self.plugin, {})
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = AddLDAPDomainStep(cclient, self.tfhelper, self.jhelper, self.plugin, {})
        assert not step.has_prompts()

    def test_enable_first_domain(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {}
        step = AddLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-channel": "2023.2/edge",
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        self.tfhelper.apply.assert_called_once_with()
        self.jhelper.wait_until_active.assert_called_once_with(
            "openstack", ["keystone", "keystone-ldap-dom1"], timeout=900
        )
        assert result.result_type == ResultType.COMPLETED

    def test_enable_second_domain(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = AddLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, {"domain-name": "dom2"}
        )
        result = step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-channel": "2023.2/edge",
                "ldap-apps": {
                    "dom1": {"domain-name": "dom1"},
                    "dom2": {"domain-name": "dom2"},
                },
            }
        )
        self.tfhelper.apply.assert_called_once_with()
        self.jhelper.wait_until_active.assert_called_once_with(
            "openstack", ["keystone", "keystone-ldap-dom2"], timeout=900
        )
        assert result.result_type == ResultType.COMPLETED

    def test_enable_tf_apply_failed(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {}
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")
        step = AddLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_enable_waiting_timed_out(self, cclient, read_config, update_config, snap):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {}
        step = AddLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-channel": "2023.2/edge",
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        self.tfhelper.apply.assert_called_once_with()
        self.jhelper.wait_until_active.assert_called_once_with(
            "openstack", ["keystone", "keystone-ldap-dom1"], timeout=900
        )
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDisableLDAPDomainStep:
    def setup_method(self):
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.charm_config = {"domain-name": "dom1"}

    def test_is_skip(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = DisableLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, "dom1"
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = DisableLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, "dom1"
        )
        assert not step.has_prompts()

    def test_disable(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, "dom1"
        )
        step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {"ldap-channel": "2023.2/edge", "ldap-apps": {}}
        )
        self.tfhelper.apply.assert_called_once_with()

    def test_disable_tf_apply_failed(self, cclient, read_config, update_config, snap):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, "dom1"
        )
        result = step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {"ldap-channel": "2023.2/edge", "ldap-apps": {}}
        )
        self.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_disable_wrong_domain(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, "dom2"
        )
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Domain not found"


class TestUpdateLDAPDomainStep:
    def setup_method(self):
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.charm_config = {"domain-name": "dom1"}

    def test_is_skip(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, cclient):
        self.plugin = FakeLDAPPlugin()
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        assert not step.has_prompts()

    def test_update_domain(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-channel": "2023.2/edge",
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        self.tfhelper.apply.assert_called_once_with()
        self.jhelper.wait_until_active.assert_called_once_with(
            "openstack", ["keystone", "keystone-ldap-dom1"], timeout=900
        )
        assert result.result_type == ResultType.COMPLETED

    def test_update_wrong_domain(self, cclient, read_config, update_config, snap):
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, {"domain-name": "dom2"}
        )
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Domain not found"

    def test_tf_apply_failed(self, cclient, read_config, update_config, snap):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_update_waiting_timed_out(self, cclient, read_config, update_config, snap):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")
        self.plugin = FakeLDAPPlugin()
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            cclient, self.tfhelper, self.jhelper, self.plugin, self.charm_config
        )
        result = step.run()
        self.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."
