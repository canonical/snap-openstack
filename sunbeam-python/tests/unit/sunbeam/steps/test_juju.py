# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pexpect
import pytest

import sunbeam.steps.juju as juju
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ModelNotFoundException

TEST_OFFER_INTERFACES = [
    "grafana_dashboard",
    "prometheus_remote_write",
    "loki_push_api",
]


@pytest.fixture()
def mock_open():
    with patch.object(Path, "open") as p:
        yield p


class TestWriteJujuStatusStep:
    def test_is_skip(self, jhelper, step_context):
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteJujuStatusStep(jhelper, "openstack", tmpfile)
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_model_not_present(self, jhelper, step_context):
        jhelper.model_exists.return_value = False
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteJujuStatusStep(jhelper, "openstack", tmpfile)
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, jhelper, step_context):
        jhelper.get_model_status.return_value = {
            "applications": {"controller": {"status": "active"}}
        }
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteJujuStatusStep(jhelper, "openstack", Path(tmpfile.name))
            result = step.run(step_context)

        jhelper.get_model_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED


class TestWriteCharmLogStep:
    def test_is_skip(self, jhelper, step_context):
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteCharmLogStep(jhelper, "openstack", tmpfile)
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_model_not_present(self, jhelper, step_context):
        jhelper.get_model.side_effect = ModelNotFoundException("not found")
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteCharmLogStep(jhelper, "openstack", tmpfile)
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, mocker, jhelper, snap, check_call, mock_open, step_context):
        mocker.patch.object(juju, "Snap", return_value=snap)
        with tempfile.NamedTemporaryFile() as tmpfile:
            step = juju.WriteCharmLogStep(jhelper, "openstack", Path(tmpfile.name))
            step.model_uuid = "test-uuid"
            result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED


class TestJujuGrantModelAccessStep:
    def test_run(self, mocker, snap, jhelper, run, step_context):
        mocker.patch.object(juju, "Snap", return_value=snap)
        jhelper.get_model_name_with_owner.return_value = "admin/control-plane"
        step = juju.JujuGrantModelAccessStep(jhelper, "fakeuser", "control-plane")
        result = step.run(step_context)

        jhelper.get_model_name_with_owner.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_model_not_exist(self, mocker, snap, jhelper, run, step_context):
        mocker.patch.object(juju, "Snap", return_value=snap)
        jhelper.get_model_name_with_owner.side_effect = ModelNotFoundException(
            "Model 'missing' not found"
        )
        step = juju.JujuGrantModelAccessStep(jhelper, "fakeuser", "missing")
        result = step.run(step_context)

        jhelper.get_model_name_with_owner.assert_called_once()
        run.assert_not_called()
        assert result.result_type == ResultType.FAILED


class TestJujuLoginStep:
    def test_is_skip_when_juju_account_not_present(self, step_context):
        step = juju.JujuLoginStep(None)
        assert step.is_skip(step_context).result_type == ResultType.SKIPPED

    def test_run(self, step_context):
        with patch(
            "sunbeam.steps.juju.pexpect.spawn",
            Mock(
                return_value=Mock(
                    __enter__=Mock(return_value=Mock(exitstatus=0)), __exit__=Mock()
                )
            ),
        ):
            step = juju.JujuLoginStep(Mock(user="test", password="test"))
            step._get_juju_binary = Mock(return_value="juju")
            assert step.is_skip(step_context).result_type == ResultType.COMPLETED

        with patch(
            "sunbeam.steps.juju.pexpect.spawn", Mock(return_value=Mock(exitstatus=0))
        ):
            result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_pexpect_timeout(self, step_context):
        with patch(
            "sunbeam.steps.juju.pexpect.spawn",
            Mock(
                return_value=Mock(
                    __enter__=Mock(return_value=Mock(exitstatus=0)), __exit__=Mock()
                )
            ),
        ):
            step = juju.JujuLoginStep(Mock(user="test", password="test"))
            step._get_juju_binary = Mock(return_value="juju")
            assert step.is_skip(step_context).result_type == ResultType.COMPLETED

        with patch(
            "sunbeam.steps.juju.pexpect.spawn",
            Mock(
                return_value=Mock(
                    exitstatus=0, expect=Mock(side_effect=pexpect.TIMEOUT("timeout"))
                )
            ),
        ):
            result = step.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_pexpect_failed_exitcode(self, step_context):
        with patch(
            "sunbeam.steps.juju.pexpect.spawn",
            Mock(
                return_value=Mock(
                    __enter__=Mock(return_value=Mock(exitstatus=0)), __exit__=Mock()
                )
            ),
        ):
            step = juju.JujuLoginStep(Mock(user="test", password="test"))
            step._get_juju_binary = Mock(return_value="juju")
            assert step.is_skip(step_context).result_type == ResultType.COMPLETED

        with patch(
            "sunbeam.steps.juju.pexpect.spawn", Mock(return_value=Mock(exitstatus=1))
        ):
            result = step.run(step_context)
        assert result.result_type == ResultType.FAILED


class TestAddCloudJujuStep:
    def test_is_skip(self, step_context):
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                cloud_name: {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition)

        with patch.object(step, "get_clouds") as mock_get_clouds:
            mock_get_clouds.side_effect = [[cloud_name]]
            result = step.is_skip(step_context)

        mock_get_clouds.assert_called_once_with("my-cloud-type", local=True)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_exception_raised(self, step_context):
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                "my-cloud": {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition)

        with patch.object(step, "get_clouds") as mock_get_clouds:
            mock_get_clouds.side_effect = subprocess.CalledProcessError(
                cmd="juju clouds", returncode=1, output="Error output"
            )

            result = step.is_skip(step_context)

        mock_get_clouds.assert_called_once_with("my-cloud-type", local=True)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_when_cloud_not_found_in_controller(self, step_context):
        controller_name = "test-controller"
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                cloud_name: {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition, controller_name)

        with patch.object(step, "get_clouds") as mock_get_clouds:
            mock_get_clouds.side_effect = [[cloud_name], []]
            result = step.is_skip(step_context)

        mock_get_clouds.assert_any_call("my-cloud-type", local=True)
        mock_get_clouds.assert_any_call(
            "my-cloud-type", local=False, controller=controller_name
        )
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_cloud_found_in_client_and_controller(self, step_context):
        controller_name = "test-controller"
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                cloud_name: {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition, controller_name)

        with patch.object(step, "get_clouds") as mock_get_clouds:
            mock_get_clouds.side_effect = [[cloud_name], [cloud_name]]
            result = step.is_skip(step_context)

        mock_get_clouds.assert_any_call("my-cloud-type", local=True)
        mock_get_clouds.assert_any_call(
            "my-cloud-type", local=False, controller=controller_name
        )
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, step_context):
        controller_name = "test-controller"
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                "my-cloud": {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition, controller_name)

        with patch.object(step, "add_cloud") as mock_add_cloud:
            mock_add_cloud.return_value = True

            result = step.run(step_context)

        mock_add_cloud.assert_called_once_with(
            "my-cloud", cloud_definition, controller_name
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_exception_raised(self, step_context):
        controller_name = "test-controller"
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                "my-cloud": {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition, controller_name)

        with patch.object(step, "add_cloud") as mock_add_cloud:
            mock_add_cloud.side_effect = subprocess.CalledProcessError(
                cmd="juju add-cloud",
                returncode=1,
                output="Error output",
                stderr="Error output",
            )

            result = step.run(step_context)

        mock_add_cloud.assert_called_once_with(
            "my-cloud", cloud_definition, controller_name
        )
        assert result.result_type == ResultType.FAILED

    def test_run_when_already_exists_in_client(self, step_context):
        controller_name = "test-controller"
        cloud_name = "my-cloud"
        cloud_definition = {
            "clouds": {
                "my-cloud": {
                    "type": "my-cloud-type",
                    # Add other required fields for the cloud definition
                }
            }
        }
        step = juju.AddCloudJujuStep(cloud_name, cloud_definition, controller_name)

        with patch.object(step, "add_cloud") as mock_add_cloud:
            mock_add_cloud.side_effect = subprocess.CalledProcessError(
                cmd="juju add-cloud",
                returncode=1,
                output="Error output",
                stderr="local cloud already exists",
            )

            result = step.run(step_context)

        mock_add_cloud.assert_called_once_with(
            "my-cloud", cloud_definition, controller_name
        )
        assert result.result_type == ResultType.COMPLETED


class TestAddCredentialsJujuStep:
    def test_is_skip(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = None

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(step, "get_credentials", return_value={})
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=True)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_credentials_exist(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = None

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "get_credentials",
            return_value={
                "client-credentials": {
                    cloud: {"cloud-credentials": {credentials: {"key": "value"}}}
                }
            },
        )
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=True)
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = None

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(step, "add_credential")
        result = step.run(step_context)

        step.add_credential.assert_called_once_with(cloud, definition, controller)
        assert result.result_type == ResultType.COMPLETED

    def test_run_failed(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = None

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "add_credential",
            side_effect=subprocess.CalledProcessError(1, "command"),
        )
        result = step.run(step_context)

        step.add_credential.assert_called_once_with(cloud, definition, controller)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_controller(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(step, "get_credentials", return_value={})
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=False)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_with_controller_when_credentials_exist(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "get_credentials",
            return_value={
                "client-credentials": {
                    cloud: {"cloud-credentials": {credentials: {"key": "value"}}}
                },
                "controller-credentials": {
                    cloud: {"cloud-credentials": {credentials: {"key": "value"}}}
                },
            },
        )
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=False)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_controller_when_crendetials_not_found_in_controller(
        self,
        mocker,
        step_context,
    ):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "get_credentials",
            side_effect=subprocess.CalledProcessError(
                1, "command", stderr="controller not found"
            ),
        )
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=False)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_with_controller_when_error_in_controller(
        self, mocker, step_context
    ):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "get_credentials",
            side_effect=subprocess.CalledProcessError(
                1, "command", stderr="unknown error"
            ),
        )
        result = step.is_skip(step_context)

        step.get_credentials.assert_called_once_with(cloud, local=False)
        assert result.result_type == ResultType.FAILED

    def test_run_with_controller(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(step, "add_credential")
        result = step.run(step_context)

        step.add_credential.assert_called_once_with(cloud, definition, controller)
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_controller_failed(self, mocker, step_context):
        cloud = "my-cloud"
        credentials = "my-credentials"
        definition = {"key": "value"}
        controller = "my-controller"

        step = juju.AddCredentialsJujuStep(cloud, credentials, definition, controller)

        mocker.patch.object(
            step,
            "add_credential",
            side_effect=subprocess.CalledProcessError(1, "command"),
        )
        result = step.run(step_context)

        step.add_credential.assert_called_once_with(cloud, definition, controller)
        assert result.result_type == ResultType.FAILED


class TestScaleJujuStep:
    def test_is_skip(self, step_context):
        step = juju.ScaleJujuStep("controller", n=3, extra_args=["--arg1", "--arg2"])
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch("subprocess.run")
    def test_run(self, mock_run, mocker, step_context):
        step = juju.ScaleJujuStep("controller", n=3, extra_args=["--arg1", "--arg2"])
        mocker.patch.object(step, "_get_juju_binary", return_value="/juju-mock")
        result = step.run(step_context)

        assert mock_run.call_count == 2
        assert result.result_type == ResultType.COMPLETED


@pytest.fixture
def add_juju_space_step() -> juju.AddJujuSpaceStep:
    jhelper = Mock()
    model = "test-model"
    space = "test-space"
    subnets = ["10.0.0.0/24", "192.168.0.0/24"]
    return juju.AddJujuSpaceStep(jhelper, model, space, subnets)


class TestAddJujuSpaceStep:
    def test_is_skip_when_spaces_are_populated(
        self,
        add_juju_space_step: juju.AddJujuSpaceStep,
        step_context,
    ):
        add_juju_space_step._wait_for_spaces = Mock(
            return_value=({"test-space": ["10.0.0.0/24", "192.168.0.0/24"]})
        )
        result = add_juju_space_step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_request_subnet_does_not_exist(
        self,
        add_juju_space_step: juju.AddJujuSpaceStep,
        step_context,
    ):
        add_juju_space_step._wait_for_spaces = Mock(
            return_value=({"test-space": ["192.168.0.0/24"]})
        )
        result = add_juju_space_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_when_spaces_are_not_populated(
        self,
        add_juju_space_step: juju.AddJujuSpaceStep,
        step_context,
    ):
        add_juju_space_step._wait_for_spaces = Mock(return_value=({}))
        result = add_juju_space_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_when_subnets_are_already_in_use(
        self,
        add_juju_space_step: juju.AddJujuSpaceStep,
        step_context,
    ):
        add_juju_space_step._wait_for_spaces = Mock(
            return_value=({"space1": ["10.0.0.0/24", "192.168.0.0/24"]})
        )
        result = add_juju_space_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run(self, add_juju_space_step: juju.AddJujuSpaceStep, step_context):
        result = add_juju_space_step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        add_juju_space_step.jhelper.add_space.assert_called_once_with(
            "test-model", "test-space", ["10.0.0.0/24", "192.168.0.0/24"]
        )


class TestUnregisterJujuControllerStep:
    def test_is_skip(self, mocker, tmp_path, step_context):
        step = juju.UnregisterJujuController("testcontroller", tmp_path)
        mocker.patch.object(step, "get_controller", return_value={"testcontroller"})
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_controller_not_registered(self, mocker, tmp_path, step_context):
        step = juju.UnregisterJujuController("testcontroller", tmp_path)
        mocker.patch.object(
            step,
            "get_controller",
            side_effect=juju.ControllerNotFoundException("Controller not found"),
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("subprocess.run")
    def test_run(self, mock_run, mocker, tmp_path, step_context):
        step = juju.UnregisterJujuController("testcontroller", tmp_path)
        mocker.patch.object(step, "_get_juju_binary", return_value="/juju-mock")
        result = step.run(step_context)
        assert mock_run.call_count == 1
        assert result.result_type == ResultType.COMPLETED

    @patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "command"))
    def test_run_unregister_failed(self, mock_run, mocker, tmp_path, step_context):
        step = juju.UnregisterJujuController("testcontroller", tmp_path)
        mocker.patch.object(step, "_get_juju_binary", return_value="/juju-mock")
        result = step.run(step_context)
        assert mock_run.call_count == 1
        assert result.result_type == ResultType.FAILED


class TestRemoveSaasApplicationsStep:
    def test_is_skip(self, jhelper, step_context):
        jhelper.get_model_status.return_value = Mock(
            app_endpoints={
                "test-1": Mock(url="admin/offering_model.test-1", endpoints={}),
                "test-2": Mock(
                    url="admin/other-model.test-1",
                    endpoints={
                        "grafana-dashboard": Mock(interface="grafana_dashboard")
                    },
                ),
                "test-3": Mock(
                    url="admin/other-model.test-2",
                    endpoints={
                        "keystone-credentials": Mock(interface="identity_credentials")
                    },
                ),
            }
        )
        step = juju.RemoveSaasApplicationsStep(
            jhelper,
            "test",
            "offering_model",
            TEST_OFFER_INTERFACES,
        )
        result = step.is_skip(step_context)
        assert step._remote_app_to_delete == ["test-1", "test-2"]
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_given_remote_app(self, jhelper, step_context):
        jhelper.get_model_status.return_value = Mock(
            app_endpoints={
                "test-1": Mock(url="admin/offering_model.test-1", endpoints={}),
                "test-2": Mock(
                    url="admin/other-model.test-1",
                    endpoints={
                        "grafana-dashboard": Mock(interface="grafana_dashboard")
                    },
                ),
                "test-3": Mock(
                    url="admin/other-model.test-2",
                    endpoints={
                        "keystone-credentials": Mock(interface="identity_credentials")
                    },
                ),
                "test-4": Mock(url="admin/offering_model.test-4", endpoints={}),
                "test-5": Mock(url="admin/offering_model.test-5", endpoints={}),
            }
        )
        step = juju.RemoveSaasApplicationsStep(
            jhelper,
            "test",
            "offering_model",
            TEST_OFFER_INTERFACES,
            ["test-4", "test-5"],
        )
        result = step.is_skip(step_context)
        assert step._remote_app_to_delete == ["test-4", "test-5"]
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_no_remote_app(self, jhelper, step_context):
        jhelper.get_model_status.return_value = Mock(app_endpoints={})
        step = juju.RemoveSaasApplicationsStep(
            jhelper,
            "test",
            "offering_model",
            TEST_OFFER_INTERFACES,
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_no_saas_app(self, jhelper, step_context):
        jhelper.get_model_status.return_value = Mock(
            app_endpoints={
                "test-1": Mock(url="admin/offering_model.test-1", endpoints={}),
                "test-3": Mock(
                    url="admin/other-model.test-2",
                    endpoints={
                        "keystone-credentials": Mock(interface="identity_credentials")
                    },
                ),
            }
        )
        step = juju.RemoveSaasApplicationsStep(
            jhelper,
            "test",
            "offering_model-no-apps",
            TEST_OFFER_INTERFACES,
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, jhelper, step_context):
        step = juju.RemoveSaasApplicationsStep(
            jhelper,
            "test",
            "offering_model",
            TEST_OFFER_INTERFACES,
        )
        step._remote_app_to_delete = ["test-1"]
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED


class TestBoostrapJujuStep:
    def test_is_skip(self, mocker, cclient, step_context):
        step = juju.BootstrapJujuStep(
            cclient, "my-cloud", "my-cloud-type", "testcontroller"
        )
        mocker.patch.object(step, "get_clouds", return_value=["my-cloud"])
        mocker.patch.object(
            step,
            "get_controller",
            side_effect=juju.ControllerNotFoundException("Controller not found"),
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_controller_already_exists(
        self, mocker, cclient, step_context
    ):
        step = juju.BootstrapJujuStep(
            cclient, "my-cloud", "my-cloud-type", "testcontroller"
        )
        mocker.patch.object(step, "get_clouds", return_value=["my-cloud"])
        mocker.patch.object(step, "get_controller", return_value="testcontroller")

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, mocker, snap, run, cclient, step_context):
        mocker.patch.object(juju, "Snap", return_value=snap)
        step = juju.BootstrapJujuStep(
            cclient, "my-cloud", "my-cloud-type", "testcontroller"
        )

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_boostrap_failed(self, mocker, snap, run, cclient, step_context):
        mocker.patch.object(juju, "Snap", return_value=snap)
        run.side_effect = subprocess.CalledProcessError(
            cmd="juju bootstrap", returncode=1, output="Error output"
        )
        step = juju.BootstrapJujuStep(
            cclient, "my-cloud", "my-cloud-type", "testcontroller"
        )

        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED


class TestResetJujuUserStep:
    @patch("subprocess.run")
    def test_parses_token_success(self, mock_run, step_context):
        step = juju.ResetJujuUserStep("test-user")
        step._juju_cmd = Mock(return_value=[{"user-name": "test-user"}])
        step._get_juju_binary = Mock(return_value="/juju")
        mock_run.return_value = Mock(stdout="", stderr="juju register new-user\n")
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert result.message == "new-user"

    @patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "command", stderr="error"),
    )
    def test_called_process_error(self, mock_run, step_context):
        step = juju.ResetJujuUserStep("test-user")
        step._juju_cmd = Mock(return_value=[{"user-name": "test-user"}])
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert mock_run.call_count == 1

    def test_user_not_found(self, step_context):
        step = juju.ResetJujuUserStep("missing-user")
        step._juju_cmd = Mock(return_value=[{"user-name": "test-user"}])
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "User missing-user not found in Juju" in result.message
