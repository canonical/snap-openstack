# Copyright (c) 2024 Canonical Ltd.
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

from unittest.mock import Mock, patch

import pytest
from watcherclient import v1 as watcher

from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.steps.maintenance import (
    RunWatcherHostMaintenanceStep,
    RunWatcherWorkloadBalancingStep,
    WatcherStepMixin,
)


@pytest.fixture(autouse=True)
def mock_watcher_helper():
    with patch("sunbeam.steps.maintenance.watcher_helper") as mock:
        yield mock


@pytest.fixture
def mock_watcher_client(mock_watcher_helper):
    mock_client = Mock()
    mock_watcher_helper.get_watcher_client.return_value = mock_client
    yield mock_client


class DummyWatcherStep(WatcherStepMixin):
    def __init__(self, mock_deployment):
        self.mock_audit = Mock()
        super().__init__(mock_deployment)

    def _create_audit(self) -> watcher.Audit:
        return self.mock_audit


class TestWatcherStepMixin:
    @patch("sunbeam.steps.maintenance.watcher_helper")
    def test_init(self, mock_watcher_helper):
        mock_deployment = Mock()
        DummyWatcherStep(mock_deployment)
        mock_watcher_helper.get_watcher_client.assert_called_once_with(
            deployment=mock_deployment
        )

    def test_prompts(self):
        step = DummyWatcherStep(Mock())
        assert step.has_prompts

    @pytest.mark.parametrize(
        "confirm,expected",
        [
            (True, ResultType.COMPLETED),
            (False, ResultType.SKIPPED),
        ],
    )
    def test_is_skip(self, confirm, expected):
        step = DummyWatcherStep(Mock())
        step.confirm = confirm
        assert step.is_skip().result_type == expected

    @patch("sunbeam.steps.maintenance.ConfirmQuestion")
    def test_confir_audit(
        self, mock_confirm_question, mock_watcher_helper, mock_watcher_client
    ):
        step = DummyWatcherStep(Mock())
        actions = []
        for action in ["a1", "a2", "a3"]:
            mock_action = Mock()
            mock_action.description = f"desc-{action}"
            mock_action.input_parameters = {"name": action}
            actions.append(mock_action)

        mock_watcher_helper.get_actions.return_value = actions

        result = step._confirm_audit(step.mock_audit)
        mock_confirm_question.assert_called_once_with(
            (
                "Confirm to run operations for cluster:\n"
                "\t0. desc-a1 | parameters: {'name': 'a1'}\n"
                "\t1. desc-a2 | parameters: {'name': 'a2'}\n"
                "\t2. desc-a3 | parameters: {'name': 'a3'}\n"
            ),
            default_value=False,
            description=(
                "This will confirm to execute the operation to enable maintenance mode"
            ),
        )
        mock_watcher_helper.get_actions.assert_called_once_with(
            client=mock_watcher_client, audit=step.mock_audit
        )
        assert result == mock_confirm_question.return_value.ask.return_value

    @patch("sunbeam.steps.maintenance.ConfirmQuestion")
    def test_confir_audit_zero_action(self, mock_confirm_question, mock_watcher_helper):
        step = DummyWatcherStep(Mock())
        mock_watcher_helper.get_actions.return_value = []

        result = step._confirm_audit(step.mock_audit)
        assert result is True
        mock_confirm_question.assert_not_called()

    def test_prompt(self):
        step = DummyWatcherStep(Mock())
        step._confirm_audit = Mock()
        step.prompt()
        assert step.audit == step.mock_audit
        assert step.confirm == step._confirm_audit.return_value

    def test_run(self, mock_watcher_helper, mock_watcher_client):
        step = DummyWatcherStep(Mock())
        step.audit = step.mock_audit
        result = step.run(status=None)
        assert result.result_type == ResultType.COMPLETED
        mock_watcher_helper.exec_audit.assert_called_once_with(
            mock_watcher_client, step.mock_audit
        )

    def test_run_failed(self, mock_watcher_helper, mock_watcher_client):
        step = DummyWatcherStep(Mock())
        step.audit = step.mock_audit
        mock_watcher_helper.exec_audit.side_effect = SunbeamException
        result = step.run(None)
        assert result.result_type == ResultType.FAILED
        mock_watcher_helper.exec_audit.assert_called_once_with(
            mock_watcher_client, step.mock_audit
        )


class TestRunWatcherHostMaintenanceStep:
    def test_create_audit(self, mock_watcher_helper, mock_watcher_client):
        step = RunWatcherHostMaintenanceStep(Mock(), node="fake-node")
        step._create_audit()
        mock_watcher_helper.get_enable_maintenance_audit_template.assert_called_once_with(
            client=mock_watcher_client
        )
        mock_watcher_helper.create_audit.assert_called_once_with(
            client=mock_watcher_client,
            template=mock_watcher_helper.get_enable_maintenance_audit_template.return_value,
            parameters={"maintenance_node": "fake-node"},
        )


class TestRunWatcherWorkloadBalancingStep:
    def test_create_audit(self, mock_watcher_helper, mock_watcher_client):
        step = RunWatcherWorkloadBalancingStep(Mock())
        step._create_audit()
        mock_watcher_helper.get_workload_balancing_audit_template.assert_called_once_with(
            client=mock_watcher_client
        )
        mock_watcher_helper.create_audit.assert_called_once_with(
            client=mock_watcher_client,
            template=mock_watcher_helper.get_workload_balancing_audit_template.return_value,
        )
