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
import json
from unittest.mock import Mock, patch

import pytest

import sunbeam.commands.generate_cloud_config as generate
import sunbeam.jobs.questions
from sunbeam.jobs.common import ResultType


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.generate_cloud_config.run_sync", run_sync)
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
def cprint():
    with patch("sunbeam.commands.generate_cloud_config.Console.print") as p:
        yield p


class TestConfigureCloudsYamlStep:
    def test_is_skip_with_demo_setup(self, tmp_path, cclient, load_answers):
        clouds_yaml = tmp_path / ".config" / "openstack" / "clouds.yaml"
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        admin_credentials = {"OS_AUTH_URL": "http://keystone:5000"}
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", False, True, clouds_yaml
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, tmp_path, cclient, load_answers):
        clouds_yaml = tmp_path / ".config" / "openstack" / "clouds.yaml"
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        admin_credentials = {"OS_AUTH_URL": "http://keystone:5000"}
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", False, True, clouds_yaml
        )
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_admin(self, tmp_path, cclient):
        clouds_yaml = tmp_path / ".config" / "openstack" / "clouds.yaml"
        admin_credentials = {"OS_AUTH_URL": "http://keystone:5000"}
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", True, True, clouds_yaml
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run(self, mocker, tmp_path, cclient, run, snap, environ):
        mocker.patch.object(generate, "Snap", return_value=snap)
        environ.copy.return_value = {}

        snap_real_home_dir = tmp_path
        environ.get.return_value = snap_real_home_dir
        clouds_yaml = snap_real_home_dir / ".config" / "openstack" / "clouds.yaml"
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
        admin_credentials = {"OS_AUTH_URL": "http://keystone:5000"}
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", False, True, clouds_yaml
        )
        step.run()

        # Verify clouds.yaml contents and assert
        with open(clouds_yaml, "r") as f:
            contents = f.read()
        expect = f"""clouds:
  sunbeam:
    auth:
      auth_url: {admin_credentials["OS_AUTH_URL"]}
      password: {creds["OS_PASSWORD"]["value"]}
      project_domain_name: {creds["OS_PROJECT_DOMAIN_NAME"]["value"]}
      project_name: {creds["OS_PROJECT_NAME"]["value"]}
      user_domain_name: {creds["OS_USER_DOMAIN_NAME"]["value"]}
      username: {creds["OS_USERNAME"]["value"]}
"""
        assert contents == expect

    def test_run_for_admin_user(self, mocker, tmp_path, cclient, run, snap, environ):
        mocker.patch.object(generate, "Snap", return_value=snap)
        environ.copy.return_value = {}

        snap_real_home_dir = tmp_path
        environ.get.return_value = snap_real_home_dir
        clouds_yaml = snap_real_home_dir / ".config" / "openstack" / "clouds.yaml"
        admin_credentials = {
            "OS_AUTH_URL": "http://keystone:5000",
            "OS_USERNAME": "admin",
            "OS_PASSWORD": "reallyhardpassword",
            "OS_USER_DOMAIN_NAME": "admindomain",
            "OS_PROJECT_DOMAIN_NAME": "projectdomain",
            "OS_PROJECT_NAME": "projectname",
        }
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", True, True, clouds_yaml
        )
        step.run()

        # Verify clouds.yaml contents and assert
        with open(clouds_yaml, "r") as f:
            contents = f.read()
        expect = f"""clouds:
  sunbeam:
    auth:
      auth_url: {admin_credentials["OS_AUTH_URL"]}
      password: {admin_credentials["OS_PASSWORD"]}
      project_domain_name: {admin_credentials["OS_PROJECT_DOMAIN_NAME"]}
      project_name: {admin_credentials["OS_PROJECT_NAME"]}
      user_domain_name: {admin_credentials["OS_USER_DOMAIN_NAME"]}
      username: {admin_credentials["OS_USERNAME"]}
"""
        assert contents == expect

    def test_run_with_update_false(self, mocker, cclient, run, snap, environ, cprint):
        mocker.patch.object(generate, "Snap", return_value=snap)
        environ.copy.return_value = {}

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
        admin_credentials = {"OS_AUTH_URL": "http://keystone:5000"}
        step = generate.GenerateCloudConfigStep(
            cclient, admin_credentials, "sunbeam", False, False, None
        )
        step.run()

        expect = f"""clouds:
  sunbeam:
    auth:
      auth_url: {admin_credentials["OS_AUTH_URL"]}
      password: {creds["OS_PASSWORD"]["value"]}
      project_domain_name: {creds["OS_PROJECT_DOMAIN_NAME"]["value"]}
      project_name: {creds["OS_PROJECT_NAME"]["value"]}
      user_domain_name: {creds["OS_USER_DOMAIN_NAME"]["value"]}
      username: {creds["OS_USERNAME"]["value"]}
"""
        cprint.assert_called_with(expect)
