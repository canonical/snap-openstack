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

import sunbeam.plugins.interface.v1.openstack as openstack
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import TimeoutException


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.plugins.interface.v1.openstack.run_sync", run_sync)
    yield
    loop.close()


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def read_config():
    with patch("sunbeam.plugins.interface.v1.openstack.read_config") as p:
        p.return_value = {}
        yield p


@pytest.fixture()
def jhelper():
    yield AsyncMock()


@pytest.fixture()
def tfhelper():
    yield Mock(path=Path())


@pytest.fixture()
def osplugin():
    with patch(
        "sunbeam.plugins.interface.v1.openstack.OpenStackControlPlanePlugin"
    ) as p:
        yield p


class TestEnableOpenStackApplicationStep:
    def test_run(
        self,
        cclient,
        read_config,
        jhelper,
        tfhelper,
        osplugin,
    ):
        step = openstack.EnableOpenStackApplicationStep(
            cclient, tfhelper, jhelper, osplugin
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, cclient, read_config, jhelper, tfhelper, osplugin
    ):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = openstack.EnableOpenStackApplicationStep(
            cclient, tfhelper, jhelper, osplugin
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, cclient, read_config, jhelper, tfhelper, osplugin
    ):
        jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = openstack.EnableOpenStackApplicationStep(
            cclient, tfhelper, jhelper, osplugin
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDisableOpenStackApplicationStep:
    def test_run(self, cclient, read_config, jhelper, tfhelper, osplugin):
        step = openstack.DisableOpenStackApplicationStep(
            cclient, tfhelper, jhelper, osplugin
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, cclient, read_config, jhelper, tfhelper, osplugin
    ):
        tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = openstack.DisableOpenStackApplicationStep(
            cclient, tfhelper, jhelper, osplugin
        )
        result = step.run()

        tfhelper.write_tfvars.assert_called_once()
        tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."
