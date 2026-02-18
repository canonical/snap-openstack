# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from unittest.mock import patch

import pytest

import sunbeam.commands.configure as configure
import sunbeam.core
from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.core.questions, "load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch.object(sunbeam.core.questions, "write_answers") as p:
        yield p


@pytest.fixture()
def question_bank():
    with patch.object(sunbeam.core.questions, "QuestionBank") as p:
        yield p


class TestUserOpenRCStep:
    def test_is_skip_with_demo(self, tmpdir, cclient, tfhelper, load_answers):
        outfile = tmpdir + "/" + "openrc"
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.UserOpenRCStep(
            cclient, tfhelper, "http://keystone:5000", "3", None, outfile
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, tmpdir, cclient, tfhelper, load_answers):
        outfile = tmpdir + "/" + "openrc"
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.UserOpenRCStep(
            cclient, tfhelper, "http://keystone:5000", "3", None, outfile
        )
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, tmpdir, cclient, tfhelper):
        outfile = tmpdir + "/" + "openrc"
        creds = {
            "OS_USERNAME": "user1",
            "OS_PASSWORD": "reallyhardpassword",
            "OS_USER_DOMAIN_NAME": "userdomain",
            "OS_PROJECT_DOMAIN_NAME": "projectdomain",
            "OS_PROJECT_NAME": "projectname",
        }
        tfhelper.output.return_value = creds
        auth_url = "http://keystone:5000"
        auth_version = 3
        step = configure.UserOpenRCStep(cclient, tfhelper, auth_url, "3", None, outfile)
        step.run()
        with open(outfile, "r") as f:
            contents = f.read()
        expect = f"""# openrc for {creds["OS_USERNAME"]}
export OS_AUTH_URL={auth_url}
export OS_USERNAME={creds["OS_USERNAME"]}
export OS_PASSWORD={creds["OS_PASSWORD"]}
export OS_USER_DOMAIN_NAME={creds["OS_USER_DOMAIN_NAME"]}
export OS_PROJECT_DOMAIN_NAME={creds["OS_PROJECT_DOMAIN_NAME"]}
export OS_PROJECT_NAME={creds["OS_PROJECT_NAME"]}
export OS_AUTH_VERSION={auth_version}
export OS_IDENTITY_API_VERSION={auth_version}"""
        assert contents == expect


class TestDemoSetup:
    def test_is_skip_demo_setup(self, cclient, tfhelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.DemoSetup(cclient, tfhelper, Path("/tmp/dummy"))
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, cclient, tfhelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.DemoSetup(cclient, tfhelper, Path("/tmp/dummy"))
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, tfhelper, load_answers):
        answer_data = {"user": {"foo": "bar"}}
        load_answers.return_value = answer_data
        step = configure.DemoSetup(cclient, tfhelper, Path("/tmp/dummy"))
        result = step.run()
        tfhelper.write_tfvars.assert_called_once_with(answer_data, Path("/tmp/dummy"))
        assert result.result_type == ResultType.COMPLETED

    def test_run_fail(self, cclient, tfhelper, load_answers):
        answer_data = {"user": {"foo": "bar"}}
        load_answers.return_value = answer_data
        tfhelper.apply.side_effect = TerraformException("Bad terraform")
        step = configure.DemoSetup(cclient, tfhelper, Path("/tmp/dummy"))
        result = step.run()
        assert result.result_type == ResultType.FAILED


class TestTerraformDemoInitStep:
    def test_is_skip_demo_setup(self, cclient, tfhelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": True}}
        step = configure.TerraformDemoInitStep(cclient, tfhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip(self, cclient, tfhelper, load_answers):
        load_answers.return_value = {"user": {"run_demo_setup": False}}
        step = configure.TerraformDemoInitStep(cclient, tfhelper)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED
