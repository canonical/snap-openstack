# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    ModelNotFoundException,
)
from sunbeam.core.terraform import TerraformException, TerraformInitStep
from sunbeam.features.pro import feature as pro_feature
from sunbeam.features.pro.feature import (
    DetachUbuntuProStep,
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
        basic_jhelper.get_app_config.return_value = {"token": test_token}
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

    @pytest.mark.parametrize(
        "error",
        [ModelNotFoundException("Model 'test-model' not found"), JujuException("boom")],
    )
    def test_enable_model_lookup_failed(
        self, enable_step, basic_jhelper, basic_tfhelper, step_context, error
    ):
        basic_jhelper.get_model_uuid.side_effect = error

        result = enable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_not_called()
        basic_jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == str(error)

    def test_enable_live_token_matches(
        self, enable_step, basic_jhelper, test_model, step_context
    ):
        result = enable_step.run(step_context)

        basic_jhelper.get_app_config.assert_called_once_with("ubuntu-pro", test_model)
        basic_jhelper.set_app_config.assert_not_called()
        basic_jhelper.wait_subordinate_units_status.assert_not_called()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_resets_token_emptied_by_failed_disable(
        self, enable_step, basic_jhelper, test_model, test_token, step_context
    ):
        basic_jhelper.get_app_config.return_value = {"token": ""}

        result = enable_step.run(step_context)

        basic_jhelper.set_app_config.assert_called_once_with(
            "ubuntu-pro", test_model, {"token": test_token}
        )
        basic_jhelper.wait_subordinate_units_status.assert_called_once_with(
            test_model,
            "sunbeam-machine",
            "ubuntu-pro",
            status=["active"],
            timeout=pro_feature.APP_TIMEOUT,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_enable_reset_token_timed_out(
        self, enable_step, basic_jhelper, step_context
    ):
        basic_jhelper.get_app_config.return_value = {"token": ""}
        basic_jhelper.wait_subordinate_units_status.side_effect = TimeoutError(
            "ubuntu-pro/0 (blocked: attach failed)"
        )

        result = enable_step.run(step_context)

        basic_jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "ubuntu-pro/0 (blocked: attach failed)"

    def test_enable_read_config_failed(self, enable_step, basic_jhelper, step_context):
        basic_jhelper.get_app_config.side_effect = JujuException("config failed")

        result = enable_step.run(step_context)

        basic_jhelper.set_app_config.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "config failed"

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


class TestDetachUbuntuProStep:
    @pytest.fixture
    def detach_step(self, basic_jhelper, test_model):
        """Detach Ubuntu Pro step instance for testing."""
        return DetachUbuntuProStep(basic_jhelper, test_model)

    def test_is_skip_app_deployed(self, detach_step, step_context):
        result = detach_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_app_missing(self, detach_step, basic_jhelper, step_context):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "missing"
        )

        result = detach_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_detach(self, detach_step, basic_jhelper, test_model, step_context):
        result = detach_step.run(step_context)

        basic_jhelper.set_app_config.assert_called_once_with(
            "ubuntu-pro", test_model, {"token": ""}
        )
        basic_jhelper.wait_subordinate_units_status.assert_called_once_with(
            test_model,
            "sunbeam-machine",
            "ubuntu-pro",
            status=["blocked"],
            workload_status_message=["No token configured"],
            timeout=pro_feature.DETACH_TIMEOUT,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_detach_app_gone_meanwhile(self, detach_step, basic_jhelper, step_context):
        basic_jhelper.set_app_config.side_effect = ApplicationNotFoundException(
            "missing"
        )

        result = detach_step.run(step_context)

        basic_jhelper.wait_subordinate_units_status.assert_not_called()
        assert result.result_type == ResultType.COMPLETED

    def test_detach_set_config_failed(self, detach_step, basic_jhelper, step_context):
        basic_jhelper.set_app_config.side_effect = JujuException("config failed")

        result = detach_step.run(step_context)

        basic_jhelper.wait_subordinate_units_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "config failed"

    def test_detach_timed_out(self, detach_step, basic_jhelper, step_context):
        basic_jhelper.wait_subordinate_units_status.side_effect = TimeoutError(
            "Timed out waiting for ubuntu-pro units: ubuntu-pro/1 (active: Attached)"
        )

        result = detach_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "ubuntu-pro/1 (active: Attached)" in result.message
        assert "was not removed" in result.message
        assert "pro detach" in result.message


class TestRunDisablePlans:
    def test_detach_runs_before_removal(self):
        deployment = Mock()
        with (
            patch.object(pro_feature, "Snap"),
            patch.object(pro_feature, "JujuHelper"),
            patch.object(pro_feature, "run_plan") as run_plan,
            patch.object(pro_feature.click, "echo"),
        ):
            feature = pro_feature.ProFeature()
            feature._manifest = Mock()
            feature.run_disable_plans(deployment, show_hints=False)

        plan = run_plan.call_args.args[0]
        assert [type(step) for step in plan] == [
            TerraformInitStep,
            DetachUbuntuProStep,
            DisableUbuntuProApplicationStep,
        ]


class TestDisableUbuntuProApplicationStep:
    @pytest.fixture
    def disable_step(
        self, basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
    ):
        """Disable Ubuntu Pro step instance for testing."""
        return DisableUbuntuProApplicationStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        )

    def test_is_skip(self, disable_step, step_context):
        result = disable_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self, disable_step):
        assert not disable_step.has_prompts()

    def test_disable(
        self,
        disable_step,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        step_context,
    ):
        basic_jhelper.get_model_uuid.return_value = "model-uuid"

        result = disable_step.run(step_context)

        basic_jhelper.get_model_uuid.assert_called_once_with(test_model)
        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            basic_client,
            basic_manifest,
            tfvar_config=None,
            override_tfvars={"machine_model_uuid": "model-uuid", "token": ""},
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

    @pytest.mark.parametrize(
        "error",
        [ModelNotFoundException("Model 'test-model' not found"), JujuException("boom")],
    )
    def test_disable_model_lookup_failed(
        self, disable_step, basic_jhelper, basic_tfhelper, step_context, error
    ):
        basic_jhelper.get_model_uuid.side_effect = error

        result = disable_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == str(error)


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


def _required_tf_variables() -> set[str]:
    """Return the deploy-pro variables that have no default value.

    A regex scan that only handles flat variable blocks, as deploy-pro has
    today. Nested blocks (e.g. validation {}), a "}" inside a string or a
    heredoc line starting with "default =" would confuse it.
    """
    variables_tf = (
        Path(pro_feature.__file__).parent / "etc" / "deploy-pro" / "variables.tf"
    )
    blocks = re.findall(
        r'variable\s+"([^"]+)"\s*\{([^}]*)\}', variables_tf.read_text(), re.DOTALL
    )
    assert blocks, "no variables found in variables.tf"
    return {
        name for name, body in blocks if not re.search(r"^\s*default\s*=", body, re.M)
    }


class TestRequiredTerraformVariables:
    """The tfvars are not persisted, so each step must pass every required var."""

    def _override_tfvars(self, basic_tfhelper) -> set[str]:
        call = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        return set(call.kwargs["override_tfvars"])

    def test_required_variables_detected(self):
        assert "machine_model_uuid" in _required_tf_variables()

    def test_enable_passes_required_variables(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        test_token,
        step_context,
    ):
        EnableUbuntuProApplicationStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_token,
            test_model,
        ).run(step_context)

        assert _required_tf_variables() <= self._override_tfvars(basic_tfhelper)

    def test_disable_passes_required_variables(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        step_context,
    ):
        DisableUbuntuProApplicationStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        ).run(step_context)

        assert _required_tf_variables() <= self._override_tfvars(basic_tfhelper)
