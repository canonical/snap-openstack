# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.features.pro import feature as pro_feature
from sunbeam.features.pro.feature import (
    DisableUbuntuProApplicationStep,
    EnableUbuntuProApplicationStep,
)

# No additional fixtures needed - using all shared fixtures


class TestEnableUbuntuProApplicationStep:
    @pytest.fixture
    def enable_step(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        test_token,
    ):
        """Enable Ubuntu Pro step instance for testing."""
        return EnableUbuntuProApplicationStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_token,
            test_model,
        )

    def test_is_skip(self, enable_step, step_context):
        result = enable_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, enable_step):
        assert not enable_step.has_prompts()

    def test_enable(
        self,
        enable_step,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        test_token,
        step_context,
    ):
        basic_jhelper.get_model_uuid.return_value = "model-uuid"

        result = enable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            basic_client,
            basic_manifest,
            tfvar_config=None,
            override_tfvars={"machine_model_uuid": "model-uuid", "token": test_token},
            reporter=step_context.reporter,
        )
        basic_jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_tf_apply_failed(self, enable_step, basic_tfhelper, step_context):
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = enable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_enable_waiting_timed_out(self, enable_step, basic_jhelper, step_context):
        basic_jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        result = enable_step.run(step_context)

        basic_jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_enable_blocked_reports_stderr(
        self, enable_step, basic_jhelper, step_context
    ):
        # Message format of ProcessExecutionError raised by the ubuntu-pro charm
        # when attaching the subscription fails.
        basic_jhelper.get_application.return_value = Mock(
            app_status=Mock(
                current="blocked",
                message=(
                    "Failed running command 'pro attach' [exit status: 1].\n"
                    "stderr: Invalid token\n"
                    "stdout: "
                ),
            )
        )

        result = enable_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message.strip() == "Invalid token"

    def test_enable_blocked_without_stderr(
        self, enable_step, basic_jhelper, step_context
    ):
        basic_jhelper.get_application.return_value = Mock(
            app_status=Mock(current="blocked", message="No token configured")
        )

        result = enable_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "No token configured"


class TestDisableUbuntuProApplicationStep:
    @pytest.fixture
    def disable_step(self, basic_client, basic_tfhelper, basic_manifest):
        """Disable Ubuntu Pro step instance for testing."""
        return DisableUbuntuProApplicationStep(
            basic_client, basic_tfhelper, basic_manifest
        )

    def test_is_skip(self, disable_step, step_context):
        result = disable_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, disable_step):
        assert not disable_step.has_prompts()

    def test_disable(
        self, disable_step, basic_client, basic_tfhelper, basic_manifest, step_context
    ):
        result = disable_step.run(step_context)
        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            basic_client,
            basic_manifest,
            tfvar_config=None,
            override_tfvars={"token": ""},
            reporter=step_context.reporter,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_disable_tf_apply_failed(self, disable_step, basic_tfhelper, step_context):
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = disable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."


class TestTerraformPlan:
    """The deploy-pro plan must deploy the ubuntu-pro charm."""

    def _plan_dir(self) -> Path:
        return Path(pro_feature.__file__).parent / "etc" / "deploy-pro"

    def test_deploys_ubuntu_pro_charm(self):
        text = (self._plan_dir() / "main.tf").read_text()
        match = re.search(r'charm\s*\{[^}]*?name\s*=\s*"([^"]+)"', text, re.DOTALL)
        assert match, "no charm block found in main.tf"
        assert match.group(1) == "ubuntu-pro"

    def test_no_ubuntu_advantage_references(self):
        for tf_file in self._plan_dir().glob("*.tf"):
            assert "ubuntu-advantage" not in tf_file.read_text(), tf_file.name

    def test_channel_default_matches_feature_constant(self):
        text = (self._plan_dir() / "variables.tf").read_text()
        match = re.search(
            r'variable\s+"ubuntu-pro-channel"\s*\{[^}]*?default\s*=\s*"([^"]+)"',
            text,
            re.DOTALL,
        )
        assert match, "no default found for ubuntu-pro-channel in variables.tf"
        assert match.group(1) == pro_feature.UBUNTU_PRO_CHANNEL
