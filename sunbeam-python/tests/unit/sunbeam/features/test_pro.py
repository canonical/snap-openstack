# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from sunbeam.core.common import ResultType
from sunbeam.core.manifest import AddManifestStep, FeatureManifest, Manifest
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
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


class TestManifestCharmConfig:
    """Charm options such as contract_url can be set via the manifest."""

    _MANIFEST = """
features:
  pro:
    software:
      charms:
        ubuntu-pro:
          revision: 46
          config:
            contract_url: https://contracts.example.com
"""

    def _variables(self) -> str:
        plan_dir = Path(pro_feature.__file__).parent / "etc" / "deploy-pro"
        return (plan_dir / "variables.tf").read_text()

    def test_default_software_registers_charm(self):
        feature = pro_feature.ProFeature()
        charms = feature.default_software_overrides().charms
        assert charms[pro_feature.APPLICATION].channel == (
            pro_feature.UBUNTU_PRO_CHANNEL
        )

    def test_tfvar_map_matches_plan_variables(self):
        feature = pro_feature.ProFeature()
        tfvar_map = feature.manifest_attributes_tfvar_map()[feature.tfplan]
        variables = self._variables()
        for tfvar in tfvar_map["charms"][pro_feature.APPLICATION].values():
            assert f'variable "{tfvar}"' in variables, tfvar

    def test_manifest_config_becomes_tfvar(self, tmp_path):
        feature = pro_feature.ProFeature()
        default = Manifest(
            features={
                feature.name: FeatureManifest(
                    software=feature.default_software_overrides()
                )
            }
        )
        override = Manifest.model_validate(yaml.safe_load(self._MANIFEST))
        override.validate_against_default(default)
        tfhelper = TerraformHelper(
            path=tmp_path,
            plan=feature.tfplan,
            tfvar_map=feature.manifest_attributes_tfvar_map()[feature.tfplan],
        )

        tfvars = tfhelper._get_tfvars(default.merge(override))

        # A user charm entry replaces the default one, so the channel falls
        # back to the variable default in variables.tf.
        assert tfvars == {
            "ubuntu-pro-revision": 46,
            "ubuntu-pro-config": {"contract_url": "https://contracts.example.com"},
        }

    def test_cli_token_wins_over_manifest_config(self):
        text = (
            Path(pro_feature.__file__).parent / "etc" / "deploy-pro" / "main.tf"
        ).read_text()
        assert re.search(
            r"config\s*=\s*merge\(\s*var\.ubuntu-pro-config\s*,\s*\{\s*"
            r"token\s*=\s*var\.token",
            text,
        )


class TestRunEnablePlans:
    """enable --manifest must store the manifest so later applies keep it."""

    @pytest.fixture
    def run_plan(self, mocker):
        mocker.patch.object(pro_feature, "JujuHelper")
        mocker.patch.object(pro_feature.click, "echo")
        return mocker.patch.object(pro_feature, "run_plan")

    def _run(self, user_manifest):
        feature = pro_feature.ProFeature()
        feature.user_manifest = user_manifest
        feature._manifest = Mock()
        feature.run_enable_plans(
            Mock(), pro_feature.ProFeatureConfig(token="t"), show_hints=False
        )

    def test_user_manifest_is_stored_first(self, run_plan, tmp_path):
        manifest_file = tmp_path / "manifest.yaml"
        self._run(manifest_file)

        plan = run_plan.call_args.args[0]
        assert [type(step) for step in plan] == [
            AddManifestStep,
            TerraformInitStep,
            EnableUbuntuProApplicationStep,
        ]
        assert plan[0].manifest_file == manifest_file

    def test_no_user_manifest_stores_nothing(self, run_plan):
        self._run(None)

        plan = run_plan.call_args.args[0]
        assert [type(step) for step in plan] == [
            TerraformInitStep,
            EnableUbuntuProApplicationStep,
        ]
