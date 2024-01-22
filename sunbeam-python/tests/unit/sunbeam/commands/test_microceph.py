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
from unittest.mock import AsyncMock, Mock

import pytest

from sunbeam.commands.microceph import ConfigureMicrocephOSDStep
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import ActionFailedException


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.microceph.run_sync", run_sync)
    yield
    loop.close()


class TestConfigureMicrocephOSDStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.client = Mock()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def test_is_skip(self):
        step = ConfigureMicrocephOSDStep(self.client, self.name, self.jhelper)
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self):
        step = ConfigureMicrocephOSDStep(self.client, self.name, self.jhelper)
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.run()

        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_action_failed(self):
        self.jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = ConfigureMicrocephOSDStep(self.client, self.name, self.jhelper)
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.run()

        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."
