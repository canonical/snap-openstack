# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0


import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
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
        basic_jhelper.get_model_owner.return_value = "admin"

        result = enable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            basic_client,
            basic_manifest,
            tfvar_config=None,
            override_tfvars={
                "machine-model": test_model,
                "machine-model-owner": "admin",
                "token": test_token,
            },
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
