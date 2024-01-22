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

from unittest.mock import AsyncMock, Mock, patch

from sunbeam.commands.upgrades.inter_channel import BaseUpgrade
from sunbeam.versions import (
    MYSQL_SERVICES_K8S,
    OPENSTACK_SERVICES_K8S,
    OVN_SERVICES_K8S,
)


class TestBaseUpgrade:
    def setup_method(self):
        self.client = Mock()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock()
        self.upgrade_service = (
            list(MYSQL_SERVICES_K8S.keys())  # noqa
            + list(OVN_SERVICES_K8S.keys())  # noqa
            + list(OPENSTACK_SERVICES_K8S.keys())  # noqa
        )

    def test_upgrade_applications(self):
        def _get_new_channel_mock(app_name, model):
            channels = {"nova": "2023.2/edge", "neutron": None}
            return channels[app_name]

        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        get_new_channel_mock = Mock()
        get_new_channel_mock.side_effect = _get_new_channel_mock
        with patch.object(BaseUpgrade, "get_new_channel", get_new_channel_mock):
            upgrader.upgrade_applications(["nova"], "openstack")
        self.jhelper.update_applications_channel.assert_called_once_with(
            "openstack",
            {
                "nova": {
                    "channel": "2023.2/edge",
                    "expected_status": {"workload": ["blocked", "active"]},
                }
            },
        )

    def test_get_new_channel_os_service(self, mocker):
        self.jhelper.get_charm_channel.return_value = "2023.1/edge"
        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        new_channel = upgrader.get_new_channel("cinder", "openstack")
        assert new_channel == "2023.2/edge"

    def test_get_new_channel_os_service_same(self, mocker):
        self.jhelper.get_charm_channel.return_value = "2023.2/edge"
        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        new_channel = upgrader.get_new_channel("cinder", "openstack")
        assert new_channel is None

    def test_get_new_channel_os_downgrade(self, mocker):
        self.jhelper.get_charm_channel.return_value = "2023.2/edge"
        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        new_channel = upgrader.get_new_channel("cinder", "openstack")
        assert new_channel is None

    def test_get_new_channel_nonos_service(self, mocker):
        self.jhelper.get_charm_channel.return_value = "3.8/stable"
        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        new_channel = upgrader.get_new_channel("rabbitmq", "openstack")
        assert new_channel == "3.12/edge"

    def test_get_new_channel_unknown(self, mocker):
        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.tfhelper,
            "openstack",
        )
        new_channel = upgrader.get_new_channel("foo", "openstack")
        assert new_channel is None
