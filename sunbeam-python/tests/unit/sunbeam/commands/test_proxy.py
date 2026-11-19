# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import pytest

from sunbeam.commands.proxy import PromptForProxyStep, _update_proxy
from sunbeam.core.common import convert_proxy_to_model_configs
from sunbeam.core.deployment import PROXY_CONFIG_KEY


@pytest.fixture()
def question_bank():
    with patch("sunbeam.commands.proxy.QuestionBank") as p:
        yield p


def mock_deployment(dep_type: str) -> Mock:
    deployment = Mock()
    deployment.type = dep_type
    deployment.get_proxy_settings.return_value = {
        "HTTP_PROXY": "http://proxy:3128",
        "HTTPS_PROXY": "http://proxy:3128",
        "NO_PROXY": ".example.com",
    }
    return deployment


@pytest.fixture()
def update_proxy_mocks():
    with (
        patch("sunbeam.commands.proxy.run_preflight_checks") as run_preflight_checks,
        patch("sunbeam.commands.proxy.update_config") as update_config,
        patch("sunbeam.commands.proxy.JujuHelper") as juju_helper,
        patch("sunbeam.commands.proxy.run_plan") as run_plan,
        patch(
            "sunbeam.commands.proxy.DeploySunbeamMachineApplicationStep"
        ) as deploy_sunbeam_machine,
        patch(
            "sunbeam.commands.proxy.UpdateJujuModelConfigStep"
        ) as update_model_config,
        patch("sunbeam.commands.proxy.TerraformInitStep") as terraform_init,
        patch(
            "sunbeam.commands.proxy.UpdateOpenStackModelConfigStep"
        ) as update_openstack_model_config,
    ):
        yield {
            "run_preflight_checks": run_preflight_checks,
            "update_config": update_config,
            "juju_helper": juju_helper,
            "run_plan": run_plan,
            "deploy_sunbeam_machine": deploy_sunbeam_machine,
            "update_model_config": update_model_config,
            "terraform_init": terraform_init,
            "update_openstack_model_config": update_openstack_model_config,
        }


class TestUpdateProxy:
    def test_local_updates_openstack_model_config(self, update_proxy_mocks):
        deployment = mock_deployment("local")
        proxy = {"proxy": {"proxy_required": True}}
        expected_model_config = convert_proxy_to_model_configs(
            deployment.get_proxy_settings.return_value
        )

        _update_proxy(proxy, deployment, False)

        update_proxy_mocks["update_config"].assert_called_once_with(
            deployment.get_client(), PROXY_CONFIG_KEY, proxy
        )
        update_proxy_mocks["deploy_sunbeam_machine"].assert_called_once()
        assert update_proxy_mocks["update_model_config"].call_count == 1
        update_proxy_mocks["update_model_config"].assert_called_once_with(
            update_proxy_mocks["juju_helper"].return_value,
            "controller",
            expected_model_config,
        )
        update_proxy_mocks["terraform_init"].assert_called_once_with(
            deployment.get_tfhelper("openstack-plan")
        )
        update_proxy_mocks["update_openstack_model_config"].assert_called_once_with(
            deployment,
            deployment.get_tfhelper("openstack-plan"),
            deployment.get_manifest(),
            expected_model_config,
        )
        update_proxy_mocks["run_plan"].assert_called_once()

    def test_maas_updates_openstack_model_config(self, update_proxy_mocks):
        deployment = mock_deployment("maas")
        proxy = {"proxy": {"proxy_required": True}}
        expected_model_config = convert_proxy_to_model_configs(
            deployment.get_proxy_settings.return_value
        )

        _update_proxy(proxy, deployment, False)

        update_proxy_mocks["update_config"].assert_called_once_with(
            deployment.get_client(), PROXY_CONFIG_KEY, proxy
        )
        update_proxy_mocks["deploy_sunbeam_machine"].assert_called_once()
        assert update_proxy_mocks["update_model_config"].call_count == 2
        update_proxy_mocks["update_model_config"].assert_any_call(
            update_proxy_mocks["juju_helper"].return_value,
            "controller",
            expected_model_config,
        )
        update_proxy_mocks["update_model_config"].assert_any_call(
            update_proxy_mocks["juju_helper"].return_value,
            deployment.openstack_machines_model,
            expected_model_config,
        )
        update_proxy_mocks["terraform_init"].assert_called_once_with(
            deployment.get_tfhelper("openstack-plan")
        )
        update_proxy_mocks["update_openstack_model_config"].assert_called_once_with(
            deployment,
            deployment.get_tfhelper("openstack-plan"),
            deployment.get_manifest(),
            expected_model_config,
        )
        update_proxy_mocks["run_plan"].assert_called_once()


class TestPromptForProxyStep:
    def test_prompt(self, question_bank):
        deployment = Mock()
        previous_answers = {}
        deployment.get_client().cluster.get_config.return_value = json.dumps(
            previous_answers
        )
        deployment.get_default_proxy_settings.return_value = {}
        question_bank().proxy_required.ask.return_value = False
        expected_write_config = {"proxy": {"proxy_required": False}}

        step = PromptForProxyStep(deployment)
        step.prompt()
        deployment.get_client().cluster.update_config.assert_called_with(
            PROXY_CONFIG_KEY, json.dumps(expected_write_config)
        )

    def test_prompt_with_previous_answers(self, question_bank):
        deployment = Mock()
        previous_answers = {
            "proxy": {
                "proxy_required": True,
                "http_proxy": "http://squid.internal:3128",
            }
        }
        deployment.get_client().cluster.get_config.return_value = json.dumps(
            previous_answers
        )
        deployment.get_default_proxy_settings.return_value = {}
        question_bank().proxy_required.ask.return_value = True
        question_bank().http_proxy.ask.return_value = "http://squid.internal:3128"
        question_bank().https_proxy.ask.return_value = "http://squid.internal:3128"
        question_bank().no_proxy.ask.return_value = ".example.com"
        expected_write_config = {
            "proxy": {
                "proxy_required": True,
                "http_proxy": "http://squid.internal:3128",
                "https_proxy": "http://squid.internal:3128",
                "no_proxy": ".example.com",
            }
        }

        step = PromptForProxyStep(deployment)
        step.prompt()
        deployment.get_client().cluster.update_config.assert_called_with(
            PROXY_CONFIG_KEY, json.dumps(expected_write_config)
        )
