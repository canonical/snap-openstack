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
import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.commands.mysql import ConfigureMySQLStep
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import JujuException


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.mysql.run_sync", run_sync)
    yield
    loop.close()


class TestConfigureMySQLStep(unittest.TestCase):
    def setUp(self):
        self.jhelper = AsyncMock()

    def test_run_pristine_installation(self):
        with patch(
            "sunbeam.commands.mysql.get_mysqls",
            Mock(return_value=["mysql"]),
        ):
            step = ConfigureMySQLStep(self.jhelper)
            result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_no_mysql(self):
        with patch(
            "sunbeam.commands.mysql.get_mysqls",
            Mock(side_effect=JujuException("No MySQL applications found")),
        ):
            step = ConfigureMySQLStep(self.jhelper)
            result = step.run()

        assert result.result_type == ResultType.FAILED

    def test_run_failed_to_get_leader(self):
        self.jhelper.get_leader_unit.side_effect = JujuException("failed to get leader")
        with patch(
            "sunbeam.commands.mysql.get_mysqls",
            Mock(return_value=["mysql"]),
        ):
            step = ConfigureMySQLStep(self.jhelper)
            result = step.run()

        assert result.result_type == ResultType.FAILED

    def test_run_failed_to_get_password(self):
        self.jhelper.run_action.side_effect = JujuException("failed to get password")
        with patch(
            "sunbeam.commands.mysql.get_mysqls",
            Mock(return_value=["mysql"]),
        ):
            step = ConfigureMySQLStep(self.jhelper)
            result = step.run()

        assert result.result_type == ResultType.FAILED

    def test_run_failed_to_set_config(self):
        self.jhelper.run_cmd_on_unit_payload.side_effect = JujuException(
            "failed to run cmd"
        )
        with patch(
            "sunbeam.commands.mysql.get_mysqls",
            Mock(return_value=["mysql"]),
        ):
            step = ConfigureMySQLStep(self.jhelper)
            result = step.run()

        assert result.result_type == ResultType.FAILED
