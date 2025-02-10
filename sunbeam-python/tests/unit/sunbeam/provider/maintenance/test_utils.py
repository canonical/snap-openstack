# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from unittest.mock import Mock, patch

from sunbeam.provider.maintenance.utils import (
    get_node_status,
)


@patch("sunbeam.provider.maintenance.utils.LocalClusterStatusStep")
@patch("sunbeam.provider.maintenance.utils.run_plan")
@patch("sunbeam.provider.maintenance.utils.get_step_message")
def test_get_node_status(
    mock_get_step_message,
    mock_run_plan,
    mock_local_cluster_status_step,
):
    mock_deployment = Mock()
    mock_deployment.type = "local"
    mock_jhelper = Mock()
    mock_get_step_message.return_value = {
        "openstack-machines": {
            "key-a": {
                "status": "target-status-val",
                "hostname": "fake-node-a",
            },
            "key-b": {
                "status": "not-target-status-val",
                "hostname": "fake-node-b",
            },
            "key-c": {
                "status": "not-target-status-val",
                "hostname": "fake-node-c",
            },
        }
    }

    result = get_node_status(
        mock_deployment, mock_jhelper, "console", False, "fake-node-a"
    )

    mock_local_cluster_status_step.assert_called_once_with(
        mock_deployment, mock_jhelper
    )
    mock_run_plan.assert_called_once_with(
        [mock_local_cluster_status_step.return_value], "console", False
    )
    mock_get_step_message.assert_called_once_with(
        mock_run_plan.return_value, mock_local_cluster_status_step
    )
    assert result == "target-status-val"
