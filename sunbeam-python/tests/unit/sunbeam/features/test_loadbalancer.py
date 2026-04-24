# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType, StepContext
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    ExecFailedException,
    JujuException,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.progress import NoOpReporter
from sunbeam.core.terraform import TerraformException
from sunbeam.features.interface.v1.base import FeatureRequirement
from sunbeam.features.loadbalancer.feature import (
    _AMPHORA_ENABLED_KEY,
    _AUTOCREATE_FLAVOR_KEY,
    _AUTOCREATE_IMAGE_KEY,
    _AUTOCREATE_NETWORK_KEY,
    _AUTOCREATE_SECGROUPS_KEY,
    _FLAVOR_KEY,
    _HEALTH_SECGROUP_KEY,
    _NETWORK_CIDR_KEY,
    _NETWORK_KEY,
    _SECGROUPS_KEY,
    _SUBNET_KEY,
    CILIUM_EXCLUSIVE_ANNOTATION,
    AmphoraConfigStep,
    CleanupMultusCNIFilesStep,
    CreateAmphoraResourcesStep,
    DeployAmphoraInfraStep,
    DestroyAmphoraResourcesStep,
    LoadbalancerAmphoraConfig,
    LoadbalancerFeature,
    LoadbalancerFeatureConfig,
    ProvideCertificatesStep,
    RemoveCNIInfraStep,
    UpdateCiliumCNIExclusiveStep,
    UpdateOctaviaAmphoraConfigStep,
)

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

FULL_RESOURCE_VARIABLES = {
    _AMPHORA_ENABLED_KEY: True,
    _AUTOCREATE_IMAGE_KEY: False,
    "amp_image_tag": "octavia-amphora",
    _AUTOCREATE_FLAVOR_KEY: False,
    _FLAVOR_KEY: "flavor-uuid",
    _AUTOCREATE_NETWORK_KEY: False,
    _NETWORK_KEY: "net-uuid",
    _SUBNET_KEY: "subnet-uuid",
    _AUTOCREATE_SECGROUPS_KEY: False,
    _SECGROUPS_KEY: ["sg-uuid"],
    _HEALTH_SECGROUP_KEY: "health-sg-uuid",
}

ALL_VARIABLES = dict(FULL_RESOURCE_VARIABLES)


def _make_context() -> StepContext:
    """Return a minimal StepContext suitable for unit tests."""
    return StepContext(status=Mock(), reporter=NoOpReporter())


def make_deployment(answers=None):
    """Return a mock Deployment whose clusterd client returns ``answers``."""
    answers = answers if answers is not None else {}
    deployment = Mock()
    deployment.get_client.return_value = Mock()
    with patch(
        "sunbeam.features.loadbalancer.feature.questions.load_answers",
        return_value=dict(answers),
    ):
        pass  # context only; callers patch individually
    return deployment


# ---------------------------------------------------------------------------
# LoadbalancerAmphoraConfig
# ---------------------------------------------------------------------------


class TestLoadbalancerAmphoraConfig:
    def test_extra_fields_ignored(self):
        """``extra='ignore'`` means unknown keys don't raise."""
        cfg = LoadbalancerAmphoraConfig(**{**ALL_VARIABLES, "unknown_key": "x"})
        assert not hasattr(cfg, "unknown_key")

    def test_defaults(self):
        cfg = LoadbalancerAmphoraConfig()
        assert cfg.amp_image_tag == "octavia-amphora"
        assert cfg.autocreate_image is False
        assert cfg.amp_flavor_id == ""

    def test_amphora_enabled_default_true(self):
        cfg = LoadbalancerAmphoraConfig()
        assert cfg.amphora_enabled is True

    def test_amphora_enabled_false(self):
        cfg = LoadbalancerAmphoraConfig(amphora_enabled=False)
        assert cfg.amphora_enabled is False


# ---------------------------------------------------------------------------
# AmphoraConfigStep.has_prompts
# ---------------------------------------------------------------------------


class TestAmphoraConfigStepHasPrompts:
    def test_has_prompts_returns_true(self):
        assert AmphoraConfigStep(Mock(), None, Mock()).has_prompts() is True


# ---------------------------------------------------------------------------
# AmphoraConfigStep._get_manifest_preseed
# ---------------------------------------------------------------------------


class TestAmphoraConfigStepGetManifestPreseed:
    def test_no_feature_config(self):
        step = AmphoraConfigStep(Mock(), None, Mock())
        assert step._get_manifest_preseed() == {}

    def test_feature_config_with_model_config(self):
        """Only explicitly-set fields are returned as preseed."""
        feature_config = LoadbalancerFeatureConfig(
            amp_image_tag="my-tag", amp_flavor_id="flavor-1"
        )
        step = AmphoraConfigStep(Mock(), feature_config, Mock())
        preseed = step._get_manifest_preseed()
        assert preseed["amp_image_tag"] == "my-tag"
        assert preseed["amp_flavor_id"] == "flavor-1"
        # Fields not explicitly set must not appear (avoids silently overriding prompts)
        assert "autocreate_network" not in preseed
        assert "autocreate_flavor" not in preseed
        assert "amphora_enabled" not in preseed

    def test_default_initialized_config_returns_empty(self):
        """A config instantiated with defaults (no explicit fields) returns {}."""
        step = AmphoraConfigStep(Mock(), LoadbalancerFeatureConfig(), Mock())
        assert step._get_manifest_preseed() == {}


# ---------------------------------------------------------------------------
# AmphoraConfigStep.run  (certificate validation)
# ---------------------------------------------------------------------------


class TestAmphoraConfigStepRun:
    def _make_step(self, stored_variables):
        deployment = Mock()
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(stored_variables),
        ):
            step = AmphoraConfigStep(deployment, None, Mock())
            step.deployment = deployment
            return step, deployment

    def test_run_returns_completed(self):
        """run() always returns COMPLETED - cert validation moved to TLS."""
        step, deployment = self._make_step(ALL_VARIABLES)
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(ALL_VARIABLES),
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_run_completed_when_amphora_disabled(self):
        """run() returns COMPLETED even when amphora is disabled."""
        variables = {_AMPHORA_ENABLED_KEY: False}
        step, deployment = self._make_step(variables)
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(variables),
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_run_completed_with_empty_config(self):
        step, deployment = self._make_step({})
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value={},
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED


# ---------------------------------------------------------------------------
# AmphoraConfigStep.prompt
# ---------------------------------------------------------------------------


def _make_bank_mock(answers: dict):
    """Build a mock QuestionBank whose per-question ask() returns answers[key]."""
    bank = Mock()
    for key, value in answers.items():
        q = Mock()
        q.ask.return_value = value
        setattr(bank, key, q)
        bank.questions = {key: q for key, value in answers.items() for key in [key]}
    return bank


class TestAmphoraConfigStepPrompt:
    """Tests for AmphoraConfigStep.prompt() covering branching logic."""

    def _run_prompt(
        self,
        stored_variables,
        resource_answers,
        accept_defaults=False,
        preseed=None,
        amphora_enabled=True,
    ):
        """Helper: run prompt() with a single mocked QuestionBank."""
        deployment = Mock()
        step = AmphoraConfigStep(
            deployment, None, Mock(), accept_defaults=accept_defaults
        )
        step._get_manifest_preseed = Mock(return_value=preseed or {})

        written_vars = {}

        def fake_write(client, section, variables):
            written_vars.update(variables)

        # Single unified bank mock — toggle + resource answers.
        bank = Mock()
        bank.amphora_enabled.ask.return_value = amphora_enabled
        bank.amp_image_tag.ask.return_value = resource_answers.get(
            "amp_image_tag", "octavia-amphora"
        )
        bank.autocreate_image.ask.return_value = resource_answers.get(
            _AUTOCREATE_IMAGE_KEY, False
        )
        # autocreate_flavor defaults to True when no flavor_id provided
        bank.autocreate_flavor.ask.return_value = resource_answers.get(
            _AUTOCREATE_FLAVOR_KEY,
            not bool(resource_answers.get(_FLAVOR_KEY, "")),
        )
        bank.amp_flavor_id.ask.return_value = resource_answers.get(_FLAVOR_KEY, "")
        # autocreate_network defaults to True when no network_id provided
        bank.autocreate_network.ask.return_value = resource_answers.get(
            _AUTOCREATE_NETWORK_KEY,
            not bool(resource_answers.get(_NETWORK_KEY, "")),
        )
        bank.lb_mgmt_cidr.ask.return_value = resource_answers.get(
            _NETWORK_CIDR_KEY, "172.31.0.0/24"
        )
        bank.lb_mgmt_network_id.ask.return_value = resource_answers.get(
            _NETWORK_KEY, ""
        )
        bank.lb_mgmt_subnet_id.ask.return_value = resource_answers.get(_SUBNET_KEY, "")
        # autocreate_securitygroups defaults to True when no secgroups provided
        bank.autocreate_securitygroups.ask.return_value = resource_answers.get(
            _AUTOCREATE_SECGROUPS_KEY,
            not bool(resource_answers.get(_SECGROUPS_KEY, "")),
        )
        bank.lb_mgmt_secgroup_ids.ask.return_value = (
            resource_answers.get(_SECGROUPS_KEY, [""])[0]
            if isinstance(resource_answers.get(_SECGROUPS_KEY), list)
            else resource_answers.get(_SECGROUPS_KEY, "")
        )
        bank.lb_health_secgroup_id.ask.return_value = resource_answers.get(
            _HEALTH_SECGROUP_KEY, ""
        )
        bank.questions = {}

        with (
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value=dict(stored_variables),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.write_answers",
                side_effect=fake_write,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.QuestionBank",
                return_value=bank,
            ),
        ):
            step.prompt()

        return written_vars

    def test_empty_network_clears_subnet(self):
        """When user provides no network, subnet should be set to empty string."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={_NETWORK_KEY: ""},  # empty network
        )
        assert result[_SUBNET_KEY] == ""

    def test_provided_network_asks_for_subnet(self):
        """When user provides a network ID, subnet question is asked."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={_NETWORK_KEY: "net-uuid", _SUBNET_KEY: "subnet-uuid"},
        )
        assert result[_SUBNET_KEY] == "subnet-uuid"

    def test_accept_defaults_uses_stored_resource_config(self):
        """accept_defaults=True passes stored answers as previous_answers."""
        stored = dict(FULL_RESOURCE_VARIABLES)
        result = self._run_prompt(
            stored_variables=stored,
            resource_answers=dict(FULL_RESOURCE_VARIABLES),
            accept_defaults=True,
        )
        assert result[_FLAVOR_KEY] == "flavor-uuid"
        assert result[_NETWORK_KEY] == "net-uuid"

    def test_secgroups_stored_as_separate_keys(self):
        """Amphora VM and health manager secgroup IDs stored under separate keys."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={
                _SECGROUPS_KEY: "sg-mgmt",
                _HEALTH_SECGROUP_KEY: "sg-health",
            },
        )
        assert result[_SECGROUPS_KEY] == ["sg-mgmt"]
        assert result[_HEALTH_SECGROUP_KEY] == "sg-health"

    def test_amphora_disabled_skips_resource_questions(self):
        """When user answers 'no' to enable question, writes disabled and returns."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={},
            amphora_enabled=False,
        )
        assert result[_AMPHORA_ENABLED_KEY] is False
        # No resource keys should be written when disabled
        assert "amp_image_tag" not in result

    def test_both_autocreate_true_clears_network_and_secgroups(self):
        """T/T: both auto-created — network and secgroup keys are all cleared."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={},  # both autocreate default to True
        )
        assert result[_NETWORK_KEY] == ""
        assert result[_SUBNET_KEY] == ""
        assert result[_SECGROUPS_KEY] == []
        assert result[_HEALTH_SECGROUP_KEY] == ""

    def test_autocreate_network_false_secgroups_true(self):
        """F/T: user provides network IDs; secgroups are auto-created (cleared)."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={
                _NETWORK_KEY: "net-uuid",
                _SUBNET_KEY: "subnet-uuid",
                # no secgroup keys → autocreate_secgroups defaults to True
            },
        )
        assert result[_NETWORK_KEY] == "net-uuid"
        assert result[_SUBNET_KEY] == "subnet-uuid"
        assert result[_SECGROUPS_KEY] == []
        assert result[_HEALTH_SECGROUP_KEY] == ""

    def test_autocreate_network_false_secgroups_false(self):
        """F/F: all four IDs user-provided; nothing is auto-created."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={
                _NETWORK_KEY: "net-uuid",
                _SUBNET_KEY: "subnet-uuid",
                _SECGROUPS_KEY: "sg-mgmt",
                _HEALTH_SECGROUP_KEY: "sg-health",
            },
        )
        assert result[_NETWORK_KEY] == "net-uuid"
        assert result[_SUBNET_KEY] == "subnet-uuid"
        assert result[_SECGROUPS_KEY] == ["sg-mgmt"]
        assert result[_HEALTH_SECGROUP_KEY] == "sg-health"

    def test_missing_network_id_raises(self):
        """autocreate_network=False + empty network_id must raise ClickException."""
        with pytest.raises(click.ClickException):
            self._run_prompt(
                stored_variables={},
                resource_answers={
                    _AUTOCREATE_NETWORK_KEY: False,
                    _NETWORK_KEY: "",
                    _SUBNET_KEY: "",
                },
            )

    def test_missing_mgmt_secgroup_raises(self):
        """autocreate_secgroups=False + empty mgmt secgroup ID raises."""
        with pytest.raises(click.ClickException):
            self._run_prompt(
                stored_variables={},
                resource_answers={
                    _AUTOCREATE_SECGROUPS_KEY: False,
                    _SECGROUPS_KEY: "",
                    _HEALTH_SECGROUP_KEY: "sg-health",
                },
            )

    def test_missing_health_secgroup_raises(self):
        """autocreate_secgroups=False + empty health secgroup ID raises."""
        with pytest.raises(click.ClickException):
            self._run_prompt(
                stored_variables={},
                resource_answers={
                    _AUTOCREATE_SECGROUPS_KEY: False,
                    _SECGROUPS_KEY: "sg-mgmt",
                    _HEALTH_SECGROUP_KEY: "",
                },
            )


# ---------------------------------------------------------------------------
# CreateAmphoraResourcesStep.is_skip
# ---------------------------------------------------------------------------


class TestCreateAmphoraResourcesStepIsSkip:
    def _make_step(self, variables):
        deployment = Mock()
        step = CreateAmphoraResourcesStep(deployment, Mock(), None)
        step.deployment = deployment
        self._variables = variables
        return step

    def _call_is_skip(self, step):
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(self._variables),
        ):
            return step.is_skip(_make_context())

    def test_all_provided_skips(self):
        step = self._make_step(FULL_RESOURCE_VARIABLES)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.SKIPPED

    def test_amphora_disabled_skips(self):
        variables = {**FULL_RESOURCE_VARIABLES, _AMPHORA_ENABLED_KEY: False}
        step = self._make_step(variables)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.SKIPPED

    def test_autocreate_flavor_not_skipped(self):
        """autocreate_flavor=True means Terraform must run — don't skip."""
        variables = {**FULL_RESOURCE_VARIABLES, _AUTOCREATE_FLAVOR_KEY: True}
        step = self._make_step(variables)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.COMPLETED

    def test_autocreate_network_not_skipped(self):
        """autocreate_network=True means Terraform must run — don't skip."""
        variables = {**FULL_RESOURCE_VARIABLES, _AUTOCREATE_NETWORK_KEY: True}
        step = self._make_step(variables)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.COMPLETED

    def test_autocreate_secgroups_not_skipped(self):
        """autocreate_securitygroups=True means Terraform must run — don't skip."""
        variables = {**FULL_RESOURCE_VARIABLES, _AUTOCREATE_SECGROUPS_KEY: True}
        step = self._make_step(variables)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.COMPLETED

    def test_autocreate_image_not_skipped(self):
        variables = {**FULL_RESOURCE_VARIABLES, _AUTOCREATE_IMAGE_KEY: True}
        step = self._make_step(variables)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.COMPLETED


# ---------------------------------------------------------------------------
# CreateAmphoraResourcesStep.run
# ---------------------------------------------------------------------------


class TestCreateAmphoraResourcesStepRun:
    def _run(self, stored_variables, tf_outputs):
        deployment = Mock()
        tfhelper = Mock()
        tfhelper.output.return_value = tf_outputs
        step = CreateAmphoraResourcesStep(deployment, tfhelper, None)

        written = {}

        with (
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value=dict(stored_variables),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.write_answers",
                side_effect=lambda _c, _s, v: written.update(v),
            ),
        ):
            result = step.run(_make_context())

        return result, written, tfhelper

    def test_all_empty_creates_everything(self):
        variables = {
            _AUTOCREATE_IMAGE_KEY: False,
            _FLAVOR_KEY: "",
            _NETWORK_KEY: "",
            _SUBNET_KEY: "",
            _SECGROUPS_KEY: [],
        }
        tf_outputs = {
            "amphora-flavor-id": "tf-flavor",
            "lb-mgmt-network-id": "tf-net",
            "lb-mgmt-subnet-id": "tf-subnet",
            "lb-mgmt-secgroup-id": "tf-sg-mgmt",
            "lb-health-secgroup-id": "tf-sg-health",
        }
        result, written, tfhelper = self._run(variables, tf_outputs)
        assert result.result_type == ResultType.COMPLETED

        # All create-* flags should be True
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["create-amphora-flavor"] is True
        assert override["create-lb-mgmt-network"] is True
        assert override["create-lb-secgroups"] is True
        assert override["create-amphora-image"] is False  # autocreate_image=False

        # Outputs fill in the empty fields
        assert written[_FLAVOR_KEY] == "tf-flavor"
        assert written[_NETWORK_KEY] == "tf-net"
        assert written[_SUBNET_KEY] == "tf-subnet"
        assert written[_SECGROUPS_KEY] == ["tf-sg-mgmt"]
        assert written[_HEALTH_SECGROUP_KEY] == "tf-sg-health"

    def test_user_provided_values_passed_to_terraform_and_returned_from_outputs(self):
        """User-provided IDs are passed as existing-* tfvars.

        Data sources look them up so outputs are always populated; written
        values reflect the real IDs.
        """
        variables = {
            _AUTOCREATE_IMAGE_KEY: False,
            _AUTOCREATE_FLAVOR_KEY: False,
            _FLAVOR_KEY: "user-flavor",
            _AUTOCREATE_NETWORK_KEY: False,
            _NETWORK_KEY: "user-net",
            _SUBNET_KEY: "user-subnet",
            _AUTOCREATE_SECGROUPS_KEY: False,
            _SECGROUPS_KEY: ["user-sg"],
            _HEALTH_SECGROUP_KEY: "user-health-sg",
        }
        # Data sources return the same IDs the user provided — outputs are
        # always populated now, not empty strings.
        tf_outputs = {
            "amphora-flavor-id": "user-flavor",
            "lb-mgmt-network-id": "user-net",
            "lb-mgmt-subnet-id": "user-subnet",
        }
        result, written, tfhelper = self._run(variables, tf_outputs)
        assert result.result_type == ResultType.COMPLETED

        # Values remain unchanged (outputs echo back the same user-provided IDs)
        assert written[_FLAVOR_KEY] == "user-flavor"
        assert written[_NETWORK_KEY] == "user-net"
        assert written[_SUBNET_KEY] == "user-subnet"
        assert written[_SECGROUPS_KEY] == ["user-sg"]

        # create-* flags should be False since all fields are provided
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["create-amphora-flavor"] is False
        assert override["create-lb-mgmt-network"] is False
        assert override["create-lb-secgroups"] is False

        # Existing IDs must be passed so TF data sources can look them up
        assert override["existing-amp-flavor-id"] == "user-flavor"
        assert override["existing-lb-mgmt-network-id"] == "user-net"
        assert override["existing-lb-mgmt-subnet-id"] == "user-subnet"

    def test_terraform_exception_returns_failed(self):
        deployment = Mock()
        tfhelper = Mock()
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException("boom")
        step = CreateAmphoraResourcesStep(deployment, tfhelper, None)

        with (
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value={},
            ),
        ):
            result = step.run(_make_context())

        assert result.result_type == ResultType.FAILED
        assert "boom" in result.message

    def test_autocreate_image_flag_passed(self):
        variables = {
            _AUTOCREATE_IMAGE_KEY: True,
            _FLAVOR_KEY: "f",
            _NETWORK_KEY: "n",
            _SUBNET_KEY: "s",
            _SECGROUPS_KEY: ["sg"],
        }
        result, written, tfhelper = self._run(variables, {})
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["create-amphora-image"] is True


# ---------------------------------------------------------------------------
# DeployAmphoraInfraStep
# ---------------------------------------------------------------------------


class TestDeployAmphoraInfraStepIsSkip:
    def _make_step(self, variables):
        deployment = Mock()
        step = DeployAmphoraInfraStep(deployment, Mock(), Mock(), None)
        step.deployment = deployment
        self._variables = variables
        return step

    def _call_is_skip(self, step):
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(self._variables),
        ):
            return step.is_skip(_make_context())

    def test_amphora_enabled_proceeds(self):
        step = self._make_step(ALL_VARIABLES)
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.COMPLETED

    def test_amphora_disabled_skips(self):
        step = self._make_step({_AMPHORA_ENABLED_KEY: False})
        result = self._call_is_skip(step)
        assert result.result_type == ResultType.SKIPPED


class TestDeployAmphoraInfraStepRun:
    def _run(self, stored_variables):
        deployment = Mock()
        tfhelper = Mock()
        jhelper = Mock()
        step = DeployAmphoraInfraStep(deployment, tfhelper, jhelper, None)

        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(stored_variables),
        ):
            result = step.run(_make_context())

        return result, tfhelper, jhelper

    def test_nad_yaml_passed_inline(self):
        """NAD yaml and model_uuid are both passed in the same Terraform apply."""
        result, tfhelper, jhelper = self._run(ALL_VARIABLES)
        assert result.result_type == ResultType.COMPLETED
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert "multus-network-attachment-definitions" in override
        assert "model_uuid" in override
        # NAD should embed the stored network and subnet IDs
        assert "net-uuid" in override["multus-network-attachment-definitions"]
        assert "subnet-uuid" in override["multus-network-attachment-definitions"]

    def test_terraform_exception_returns_failed(self):
        deployment = Mock()
        tfhelper = Mock()
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException("tf-err")
        step = DeployAmphoraInfraStep(deployment, tfhelper, Mock(), None)

        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(ALL_VARIABLES),
        ):
            result = step.run(_make_context())

        assert result.result_type == ResultType.FAILED
        assert "tf-err" in result.message

    def test_juju_wait_timeout_returns_failed(self):
        deployment = Mock()
        tfhelper = Mock()
        jhelper = Mock()
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")
        step = DeployAmphoraInfraStep(deployment, tfhelper, jhelper, None)

        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=dict(ALL_VARIABLES),
        ):
            result = step.run(_make_context())

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message


# ---------------------------------------------------------------------------
# UpdateOctaviaAmphoraConfigStep.run
# ---------------------------------------------------------------------------


class TestUpdateOctaviaAmphoraConfigStepRun:
    def _make_step(self, stored_variables):
        deployment = Mock()
        openstack_tfhelper = Mock()
        jhelper = Mock()
        step = UpdateOctaviaAmphoraConfigStep(
            deployment, openstack_tfhelper, jhelper, None
        )
        step._stored = dict(stored_variables)
        return step, openstack_tfhelper, jhelper

    def _run(self, step):
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value=step._stored,
        ):
            return step.run(_make_context())

    def test_no_config_returns_completed(self):
        """Empty config returns COMPLETED — certs no longer required in charm config."""
        step, _, _ = self._make_step({})
        result = self._run(step)
        assert result.result_type == ResultType.COMPLETED

    def test_amphora_disabled_clears_octavia_config(self):
        """When disabled, UpdateOctaviaAmphoraConfigStep actively clears.

        The Octavia charm config is reset by applying an empty octavia-config.
        """
        step, openstack_tf, _ = self._make_step({_AMPHORA_ENABLED_KEY: False})
        result = self._run(step)
        assert result.result_type == ResultType.COMPLETED
        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]
        assert octavia_config == {}

    def test_amphora_disabled_waits_for_octavia_to_settle(self):
        """Even in the disable path, we wait for Octavia to reach active/blocked."""
        step, _, jhelper = self._make_step({_AMPHORA_ENABLED_KEY: False})
        result = self._run(step)
        assert result.result_type == ResultType.COMPLETED
        jhelper.wait_until_desired_status.assert_called_once()

    def test_success_builds_correct_octavia_config(self):
        step, openstack_tf, jhelper = self._make_step(ALL_VARIABLES)
        result = self._run(step)
        assert result.result_type == ResultType.COMPLETED

        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]

        assert octavia_config["amp-image-tag"] == "octavia-amphora"
        # Certificates are provisioned via TLS relation, not charm config
        assert "lb-mgmt-issuing-cacert" not in octavia_config
        assert "lb-mgmt-issuing-ca-private-key" not in octavia_config
        assert "lb-mgmt-issuing-ca-key-passphrase" not in octavia_config
        assert "lb-mgmt-controller-cacert" not in octavia_config
        assert "lb-mgmt-controller-cert" not in octavia_config

    def test_optional_flavor_included_when_set(self):
        step, openstack_tf, _ = self._make_step(ALL_VARIABLES)
        self._run(step)
        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]
        assert "amp-flavor-id" in octavia_config
        assert octavia_config["amp-flavor-id"] == "flavor-uuid"

    def test_optional_flavor_omitted_when_empty(self):
        variables = {**ALL_VARIABLES, _FLAVOR_KEY: ""}
        step, openstack_tf, _ = self._make_step(variables)
        self._run(step)
        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]
        assert "amp-flavor-id" not in octavia_config

    def test_optional_secgroups_included_when_set(self):
        step, openstack_tf, _ = self._make_step(ALL_VARIABLES)
        self._run(step)
        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]
        assert octavia_config["amp-secgroup-list"] == "sg-uuid"

    def test_optional_network_included_when_set(self):
        step, openstack_tf, _ = self._make_step(ALL_VARIABLES)
        self._run(step)
        octavia_config = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]["octavia-config"]
        assert octavia_config["amp-boot-network-list"] == "net-uuid"

    def test_openstack_terraform_exception_returns_failed(self):
        step, openstack_tf, _ = self._make_step(ALL_VARIABLES)
        openstack_tf.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "os-err"
        )
        result = self._run(step)
        assert result.result_type == ResultType.FAILED
        assert "os-err" in result.message

    def test_tls_provider_set_when_amphora_enabled(self):
        """octavia-to-tls-provider is set to manual-tls-certificates when enabled."""
        step, openstack_tf, _ = self._make_step(ALL_VARIABLES)
        self._run(step)
        override = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["octavia-to-tls-provider"] == "manual-tls-certificates"

    def test_tls_provider_cleared_when_amphora_disabled(self):
        """octavia-to-tls-provider is cleared to None (null) when disabled."""
        step, openstack_tf, _ = self._make_step({_AMPHORA_ENABLED_KEY: False})
        self._run(step)
        override = openstack_tf.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["octavia-to-tls-provider"] is None

    def test_juju_wait_octavia_timeout_returns_failed(self):
        step, openstack_tf, jhelper = self._make_step(ALL_VARIABLES)
        jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")
        result = self._run(step)
        assert result.result_type == ResultType.FAILED


# ---------------------------------------------------------------------------
# LoadbalancerFeature — requires property and enabled_commands
# ---------------------------------------------------------------------------


class TestLoadbalancerFeatureRequires:
    """Test the dynamic ``requires`` property on LoadbalancerFeature."""

    def _make_feature(self):
        return LoadbalancerFeature()

    def test_requires_empty_when_gate_disabled(self):
        """No FeatureRequirement when loadbalancer-amphora gate is off."""
        feature = self._make_feature()
        with patch(
            "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
            return_value=False,
        ):
            assert feature.requires == set()

    def test_requires_secrets_when_gate_enabled(self):
        """FeatureRequirement('secrets') returned when gate is on."""
        feature = self._make_feature()
        with (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                side_effect=Exception("not a snap"),
            ),
        ):
            assert feature.requires == {FeatureRequirement("secrets")}


class TestLoadbalancerFeatureEnabledCommands:
    """Test that enabled_commands() always registers all amphora commands.

    The @feature_gate_command decorator is evaluated at class definition time
    (import time), so it handles hiding/erroring at invocation — the commands
    are unconditionally present in the enabled_commands() dict.
    """

    def _make_feature(self):
        return LoadbalancerFeature()

    def test_all_amphora_commands_always_registered(self):
        """configure/provide_certificates/list_outstanding_csrs always in dict."""
        feature = self._make_feature()
        commands = feature.enabled_commands()
        amphora_names = [c["name"] for c in commands.get("init.loadbalancer", [])]
        assert "configure" in amphora_names
        assert "provide_certificates" in amphora_names
        assert "list_outstanding_csrs" in amphora_names

    def test_loadbalancer_group_always_registered(self):
        """The loadbalancer click group is always present."""
        feature = self._make_feature()
        commands = feature.enabled_commands()
        init_names = [c["name"] for c in commands.get("init", [])]
        assert "loadbalancer" in init_names


# ---------------------------------------------------------------------------
# Secrets prerequisite check in amphora CLI commands
# ---------------------------------------------------------------------------


class TestAmphoraCommandsSecretsPrerequisite:
    """Verify that configure/provide_certificates/list_outstanding_csrs raise.

    click.ClickException when the secrets feature is not enabled.

    Commands use @pass_method_obj which reads deployment from the click context
    object, so we patch click.get_current_context and call .callback(None, ...)
    where None is the ignored ``self`` arg.
    """

    def _make_feature(self):
        return LoadbalancerFeature()

    def _make_ctx(self, secrets_enabled: bool):
        deployment = Mock()
        fm = Mock()
        fm.is_feature_enabled.return_value = secrets_enabled
        deployment.get_feature_manager.return_value = fm
        ctx = Mock()
        ctx.obj = deployment
        return ctx

    def test_configure_raises_when_secrets_disabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=False)
        with patch("click.get_current_context", return_value=ctx):
            with pytest.raises(click.ClickException, match="secrets"):
                feature.configure.callback(
                    None, accept_defaults=False, show_hints=False
                )

    def test_configure_proceeds_when_secrets_enabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=True)
        with (
            patch("click.get_current_context", return_value=ctx),
            patch.object(feature, "run_configure_plans") as mock_run,
        ):
            feature.configure.callback(feature, accept_defaults=False, show_hints=False)
            mock_run.assert_called_once()

    def test_provide_certificates_raises_when_secrets_disabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=False)
        with patch("click.get_current_context", return_value=ctx):
            with pytest.raises(click.ClickException, match="secrets"):
                feature.provide_certificates.callback(None, show_hints=False)

    def test_provide_certificates_proceeds_when_secrets_enabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=True)
        with (
            patch("click.get_current_context", return_value=ctx),
            patch("sunbeam.features.loadbalancer.feature.run_plan") as mock_run,
            patch("sunbeam.features.loadbalancer.feature.JujuHelper"),
        ):
            feature.provide_certificates.callback(feature, show_hints=False)
            mock_run.assert_called_once()

    def test_list_outstanding_csrs_raises_when_secrets_disabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=False)
        with patch("click.get_current_context", return_value=ctx):
            with pytest.raises(click.ClickException, match="secrets"):
                feature.list_outstanding_csrs.callback(None, format="table")

    def test_list_outstanding_csrs_proceeds_when_secrets_enabled(self):
        feature = self._make_feature()
        ctx = self._make_ctx(secrets_enabled=True)
        with (
            patch("click.get_current_context", return_value=ctx),
            patch(
                "sunbeam.features.loadbalancer.feature.handle_list_outstanding_csrs",
                return_value=[],
            ),
        ):
            feature.list_outstanding_csrs.callback(feature, format="table")


# ---------------------------------------------------------------------------
# run_configure_plans — early-exit when amphora was never enabled
# ---------------------------------------------------------------------------


class TestRunConfigurePlansEarlyExit:
    """Tests for run_configure_plans early-exit when amphora was never enabled."""

    def _make_feature(self):
        return LoadbalancerFeature()

    def test_no_teardown_when_previously_not_enabled(self):
        """Disable path is skipped entirely when amphora was never configured."""
        from unittest.mock import PropertyMock

        feature = self._make_feature()
        deployment = Mock()

        # Simulate: never configured before → empty previous answers.
        # After AmphoraConfigStep (mocked via run_plan), the stored state is disabled.
        answers_sequence = [
            {},  # load_answers call before AmphoraConfigStep (previous state)
            {_AMPHORA_ENABLED_KEY: False},  # load_answers call after AmphoraConfigStep
        ]
        load_answers_iter = iter(answers_sequence)

        with (
            patch.object(
                type(feature), "manifest", new_callable=PropertyMock, return_value=None
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                side_effect=lambda *_: dict(next(load_answers_iter)),
            ),
            patch("sunbeam.features.loadbalancer.feature.run_plan") as mock_run_plan,
            patch("sunbeam.features.loadbalancer.feature.JujuHelper"),
            patch("click.echo") as mock_echo,
        ):
            feature.run_configure_plans(deployment, show_hints=False)

        # run_plan should be called exactly once (for AmphoraConfigStep only).
        assert mock_run_plan.call_count == 1
        mock_echo.assert_called_once_with("Octavia Amphora provider disabled.")

    def test_teardown_runs_when_previously_enabled(self):
        """Disable path steps ARE run when amphora was previously enabled."""
        from unittest.mock import PropertyMock

        feature = self._make_feature()
        deployment = Mock()

        answers_sequence = [
            {_AMPHORA_ENABLED_KEY: True},  # previous state: was enabled
            {_AMPHORA_ENABLED_KEY: False},  # new state: user said no
        ]
        load_answers_iter = iter(answers_sequence)

        with (
            patch.object(
                type(feature), "manifest", new_callable=PropertyMock, return_value=None
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                side_effect=lambda *_: dict(next(load_answers_iter)),
            ),
            patch("sunbeam.features.loadbalancer.feature.run_plan") as mock_run_plan,
            patch("sunbeam.features.loadbalancer.feature.JujuHelper"),
            patch("click.echo"),
        ):
            feature.run_configure_plans(deployment, show_hints=False)

        # run_plan is called twice: once for prompts, once for the teardown plan.
        assert mock_run_plan.call_count == 2


# ---------------------------------------------------------------------------
# RemoveCNIInfraStep
# ---------------------------------------------------------------------------


class TestRemoveCNIInfraStepIsSkip:
    def _make_step(self, tf_state=None, tf_raises=False):
        deployment = Mock()
        tfhelper = Mock()
        if tf_raises:
            tfhelper.pull_state.side_effect = TerraformException("no state")
        else:
            tfhelper.pull_state.return_value = tf_state or {}
        step = RemoveCNIInfraStep(deployment, tfhelper, Mock())
        return step

    def test_terraform_exception_skips(self):
        step = self._make_step(tf_raises=True)
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_empty_state_skips(self):
        step = self._make_step(tf_state={"resources": []})
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_no_resources_key_skips(self):
        step = self._make_step(tf_state={})
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_has_resources_proceeds(self):
        step = self._make_step(tf_state={"resources": [{"type": "juju_application"}]})
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.COMPLETED


class TestRemoveCNIInfraStepRun:
    def _make_step(self, tf_raises=False, wait_raises=False):
        deployment = Mock()
        tfhelper = Mock()
        jhelper = Mock()
        if tf_raises:
            tfhelper.destroy.side_effect = TerraformException("destroy failed")
        if wait_raises:
            jhelper.wait_application_gone.side_effect = TimeoutError("timed out")
        step = RemoveCNIInfraStep(deployment, tfhelper, jhelper)
        return step, tfhelper, jhelper

    def test_success(self):
        step, tfhelper, jhelper = self._make_step()
        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()

    def test_terraform_exception_returns_failed(self):
        step, _, _ = self._make_step(tf_raises=True)
        result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED
        assert "destroy failed" in result.message

    def test_timeout_returns_failed(self):
        step, _, _ = self._make_step(wait_raises=True)
        result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message


# ---------------------------------------------------------------------------
# CleanupMultusCNIFilesStep
# ---------------------------------------------------------------------------


class TestCleanupMultusCNIFilesStep:
    def _make_step(self):
        deployment = Mock()
        jhelper = Mock()
        step = CleanupMultusCNIFilesStep(deployment, jhelper)
        return step, deployment, jhelper

    def test_app_not_found_returns_completed(self):
        """ApplicationNotFoundException is non-fatal — step returns COMPLETED."""
        step, _, jhelper = self._make_step()
        jhelper.get_application.side_effect = ApplicationNotFoundException("no k8s")
        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_juju_exception_returns_completed(self):
        """JujuException is non-fatal — step returns COMPLETED."""
        step, _, jhelper = self._make_step()
        jhelper.get_application.side_effect = JujuException("juju error")
        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_success_runs_rm_on_all_units(self):
        """Cleanup runs rm command on every unit and returns COMPLETED."""
        step, deployment, jhelper = self._make_step()
        app = Mock()
        app.units = ["k8s/0", "k8s/1"]
        jhelper.get_application.return_value = app

        cmd_result = Mock()
        cmd_result.return_code = 0
        jhelper.run_cmd_on_machine_unit_payload.return_value = cmd_result

        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 2
        # Verify the command removes the multus conf file
        call_cmd = jhelper.run_cmd_on_machine_unit_payload.call_args_list[0][0][2]
        assert "00-multus.conf" in call_cmd

    def test_exec_failure_on_one_unit_continues(self):
        """An exec failure on one unit is logged but does not stop the step."""
        step, deployment, jhelper = self._make_step()
        app = Mock()
        app.units = ["k8s/0", "k8s/1"]
        jhelper.get_application.return_value = app

        # First unit fails, second succeeds
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            ExecFailedException("exec failed"),
            Mock(return_code=0),
        ]

        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 2

    def test_nonzero_exit_code_logged_but_completes(self):
        """Non-zero exit code from rm is logged as warning but step completes."""
        step, _, jhelper = self._make_step()
        app = Mock()
        app.units = ["k8s/0"]
        jhelper.get_application.return_value = app

        cmd_result = Mock()
        cmd_result.return_code = 1
        cmd_result.stderr = "permission denied"
        jhelper.run_cmd_on_machine_unit_payload.return_value = cmd_result

        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED


# ---------------------------------------------------------------------------
# DestroyAmphoraResourcesStep
# ---------------------------------------------------------------------------


class TestDestroyAmphoraResourcesStepIsSkip:
    def _make_step(self, tf_state=None, tf_raises=False):
        deployment = Mock()
        tfhelper = Mock()
        if tf_raises:
            tfhelper.pull_state.side_effect = TerraformException("no state")
        else:
            tfhelper.pull_state.return_value = tf_state or {}
        return DestroyAmphoraResourcesStep(deployment, tfhelper)

    def test_terraform_exception_skips(self):
        step = self._make_step(tf_raises=True)
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_empty_resources_skips(self):
        step = self._make_step(tf_state={"resources": []})
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_has_resources_proceeds(self):
        step = self._make_step(
            tf_state={"resources": [{"type": "openstack_compute_flavor_v2"}]}
        )
        result = step.is_skip(_make_context())
        assert result.result_type == ResultType.COMPLETED


class TestDestroyAmphoraResourcesStepRun:
    def _make_step(self, tf_raises=False):
        deployment = Mock()
        tfhelper = Mock()
        if tf_raises:
            tfhelper.destroy.side_effect = TerraformException("destroy failed")
        step = DestroyAmphoraResourcesStep(deployment, tfhelper)
        return step, tfhelper

    def test_success(self):
        step, tfhelper = self._make_step()
        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        tfhelper.destroy.assert_called_once()

    def test_terraform_exception_returns_failed(self):
        step, _ = self._make_step(tf_raises=True)
        result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED
        assert "destroy failed" in result.message


# ---------------------------------------------------------------------------
# ProvideCertificatesStep
# ---------------------------------------------------------------------------


class TestProvideCertificatesStepIsSkip:
    def _make_step(self, amphora_enabled):
        deployment = Mock()
        step = ProvideCertificatesStep(deployment, None, Mock())
        with patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            return_value={_AMPHORA_ENABLED_KEY: amphora_enabled},
        ):
            result = step.is_skip(_make_context())
        return result

    def test_amphora_disabled_skips(self):
        result = self._make_step(amphora_enabled=False)
        assert result.result_type == ResultType.SKIPPED

    def test_amphora_enabled_proceeds(self):
        result = self._make_step(amphora_enabled=True)
        assert result.result_type == ResultType.COMPLETED


class TestProvideCertificatesStepRun:
    def _make_step(self):
        deployment = Mock()
        jhelper = Mock()
        step = ProvideCertificatesStep(deployment, None, jhelper)
        return step, jhelper

    def test_no_certs_octavia_active_returns_completed(self):
        """No certs to provide and Octavia is active → COMPLETED."""
        step, jhelper = self._make_step()
        step.process_certs = {}
        result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        jhelper.wait_until_active.assert_called_once()

    def test_no_certs_octavia_not_active_returns_failed(self):
        """No certs to provide but Octavia is not active → FAILED."""
        step, jhelper = self._make_step()
        step.process_certs = {}
        jhelper.wait_until_active.side_effect = JujuWaitException("not active")
        result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED
        assert "No outstanding certificate requests" in result.message

    def test_leader_not_found_returns_failed(self):
        """LeaderNotFoundException when getting unit → FAILED."""
        step, jhelper = self._make_step()
        step.process_certs = {
            "subject1": {
                "app": "octavia",
                "unit": "octavia/0",
                "relation_id": "1",
                "csr": "csr-data",
                "certificate": "cert",
                "ca_cert": "ca",
                "ca_chain": "",
            }
        }
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")
        result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED

    def test_action_failed_returns_failed(self):
        """ActionFailedException from provide-certificate → FAILED."""
        step, jhelper = self._make_step()
        step.process_certs = {
            "subject1": {
                "app": "octavia",
                "unit": "octavia/0",
                "relation_id": "1",
                "csr": "csr-pem-data",
                "certificate": "cert",
                "ca_cert": "ca",
                "ca_chain": "",
            }
        }
        jhelper.get_leader_unit.return_value = "octavia/0"
        jhelper.run_action.side_effect = ActionFailedException("action failed")

        with patch(
            "sunbeam.features.loadbalancer.feature.encode_base64_as_string",
            return_value="encoded-csr",
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED

    def test_provide_cert_success_waits_for_active(self):
        """Successful provide-cert waits for Octavia to become active."""
        step, jhelper = self._make_step()
        step.process_certs = {
            "subject1": {
                "app": "octavia",
                "unit": "octavia/0",
                "relation_id": "1",
                "csr": "csr-pem-data",
                "certificate": "cert",
                "ca_cert": "ca",
                "ca_chain": "",
            }
        }
        jhelper.get_leader_unit.return_value = "octavia/0"
        jhelper.run_action.return_value = {"return-code": 0}

        with patch(
            "sunbeam.features.loadbalancer.feature.encode_base64_as_string",
            return_value="encoded-csr",
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.COMPLETED
        jhelper.wait_until_active.assert_called_once()


# ---------------------------------------------------------------------------
# UpdateCiliumCNIExclusiveStep
# ---------------------------------------------------------------------------


class TestUpdateCiliumCNIExclusiveStepIsSkip:
    def _make_step(
        self, enable_multus, amphora_enabled=True, k8s_config=None, k8s_raises=False
    ):
        deployment = Mock()
        k8s_tfhelper = Mock()
        jhelper = Mock()
        step = UpdateCiliumCNIExclusiveStep(
            deployment, k8s_tfhelper, jhelper, None, enable_multus=enable_multus
        )

        def load_answers_side_effect(client, section):
            return {_AMPHORA_ENABLED_KEY: amphora_enabled}

        def read_config_side_effect(client, key):
            if k8s_raises:
                raise ConfigItemNotFoundException("not found")
            return k8s_config or {}

        self._load_answers_patch = patch(
            "sunbeam.features.loadbalancer.feature.questions.load_answers",
            side_effect=load_answers_side_effect,
        )
        self._read_config_patch = patch(
            "sunbeam.features.loadbalancer.feature.read_config",
            side_effect=read_config_side_effect,
        )
        return step

    def test_enable_multus_amphora_disabled_skips(self):
        step = self._make_step(enable_multus=True, amphora_enabled=False)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_enable_multus_no_k8s_config_proceeds(self):
        step = self._make_step(enable_multus=True, k8s_raises=True)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_enable_multus_annotation_already_present_skips(self):
        k8s_config = {
            "k8s_config": {"cluster-annotations": CILIUM_EXCLUSIVE_ANNOTATION}
        }
        step = self._make_step(enable_multus=True, k8s_config=k8s_config)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_enable_multus_annotation_absent_proceeds(self):
        k8s_config = {"k8s_config": {"cluster-annotations": ""}}
        step = self._make_step(enable_multus=True, k8s_config=k8s_config)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.COMPLETED

    def test_disable_multus_annotation_absent_skips(self):
        k8s_config = {"k8s_config": {"cluster-annotations": ""}}
        step = self._make_step(enable_multus=False, k8s_config=k8s_config)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_disable_multus_no_k8s_config_skips(self):
        step = self._make_step(enable_multus=False, k8s_raises=True)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.SKIPPED

    def test_disable_multus_annotation_present_proceeds(self):
        k8s_config = {
            "k8s_config": {"cluster-annotations": CILIUM_EXCLUSIVE_ANNOTATION}
        }
        step = self._make_step(enable_multus=False, k8s_config=k8s_config)
        with self._load_answers_patch, self._read_config_patch:
            result = step.is_skip(_make_context())
        assert result.result_type == ResultType.COMPLETED


class TestUpdateCiliumCNIExclusiveStepRun:
    def _run(self, enable_multus, existing_annotations=""):
        deployment = Mock()
        k8s_tfhelper = Mock()
        jhelper = Mock()
        step = UpdateCiliumCNIExclusiveStep(
            deployment, k8s_tfhelper, jhelper, None, enable_multus=enable_multus
        )

        k8s_config = {"k8s_config": {"cluster-annotations": existing_annotations}}

        with patch(
            "sunbeam.features.loadbalancer.feature.read_config",
            return_value=k8s_config,
        ):
            result = step.run(_make_context())

        override = k8s_tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        return result, override

    def test_enable_multus_adds_annotation(self):
        result, override = self._run(enable_multus=True, existing_annotations="")
        assert result.result_type == ResultType.COMPLETED
        assert (
            CILIUM_EXCLUSIVE_ANNOTATION in override["k8s_config"]["cluster-annotations"]
        )

    def test_disable_multus_removes_annotation(self):
        existing = f"other-annotation {CILIUM_EXCLUSIVE_ANNOTATION}"
        result, override = self._run(enable_multus=False, existing_annotations=existing)
        assert result.result_type == ResultType.COMPLETED
        assert (
            CILIUM_EXCLUSIVE_ANNOTATION
            not in override["k8s_config"]["cluster-annotations"]
        )
        assert "other-annotation" in override["k8s_config"]["cluster-annotations"]

    def test_terraform_exception_returns_failed(self):
        deployment = Mock()
        k8s_tfhelper = Mock()
        jhelper = Mock()
        k8s_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "tf-err"
        )
        step = UpdateCiliumCNIExclusiveStep(
            deployment, k8s_tfhelper, jhelper, None, enable_multus=True
        )

        with patch(
            "sunbeam.features.loadbalancer.feature.read_config",
            return_value={"k8s_config": {"cluster-annotations": ""}},
        ):
            result = step.run(_make_context())
        assert result.result_type == ResultType.FAILED
        assert "tf-err" in result.message


# ---------------------------------------------------------------------------
# run_configure_plans — disable path includes CleanupMultusCNIFilesStep
# ---------------------------------------------------------------------------


class TestRunConfigurePlansDisableIncludesCleanup:
    """Verify CleanupMultusCNIFilesStep is part of the configure-disable plan."""

    def _make_feature(self):
        return LoadbalancerFeature()

    def test_cleanup_step_in_configure_disable_plan(self):
        """The configure-disable plan must include CleanupMultusCNIFilesStep."""
        from unittest.mock import PropertyMock

        feature = self._make_feature()
        deployment = Mock()

        answers_sequence = [
            {_AMPHORA_ENABLED_KEY: True},  # previous state: was enabled
            {_AMPHORA_ENABLED_KEY: False},  # new state: user chose to disable
        ]
        load_answers_iter = iter(answers_sequence)

        captured_plans = []

        def capture_run_plan(plan, *args, **kwargs):
            captured_plans.append(list(plan))

        with (
            patch.object(
                type(feature), "manifest", new_callable=PropertyMock, return_value=None
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                side_effect=lambda *_: dict(next(load_answers_iter)),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.run_plan",
                side_effect=capture_run_plan,
            ),
            patch("sunbeam.features.loadbalancer.feature.JujuHelper"),
            patch("click.echo"),
        ):
            feature.run_configure_plans(deployment, show_hints=False)

        # Second call is the disable plan — find CleanupMultusCNIFilesStep in it
        assert len(captured_plans) == 2
        disable_plan = captured_plans[1]
        step_types = [type(s).__name__ for s in disable_plan]
        assert "CleanupMultusCNIFilesStep" in step_types
        # Verify ordering: cleanup comes after RemoveCNIInfraStep
        remove_idx = step_types.index("RemoveCNIInfraStep")
        cleanup_idx = step_types.index("CleanupMultusCNIFilesStep")
        assert cleanup_idx == remove_idx + 1


class TestLoadbalancerFeatureRequiresClusterd:
    """Verify requires reads amphora_enabled from clusterd via Client.from_socket."""

    def _make_feature(self):
        return LoadbalancerFeature()

    def _gate_on_socket(self, feature, load_answers_return):
        """Helper: patch gate=True and Client.from_socket + load_answers."""
        return (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                return_value=Mock(),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value=load_answers_return,
            ),
        )

    def test_requires_secrets_when_amphora_enabled_in_clusterd(self):
        """Requires secrets when clusterd says amphora_enabled=True."""
        feature = self._make_feature()
        with (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                return_value=Mock(),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value={_AMPHORA_ENABLED_KEY: True},
            ),
        ):
            reqs = feature.requires
        assert len(reqs) == 1
        assert next(iter(reqs)).name == "secrets"

    def test_requires_empty_when_amphora_disabled_in_clusterd(self):
        """No requirements when clusterd says amphora_enabled=False."""
        feature = self._make_feature()
        with (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                return_value=Mock(),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value={_AMPHORA_ENABLED_KEY: False},
            ),
        ):
            assert feature.requires == set()

    def test_requires_empty_when_clusterd_key_absent(self):
        """No requirements when key is absent (e.g. after post_disable deleted it)."""
        feature = self._make_feature()
        with (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                return_value=Mock(),
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.questions.load_answers",
                return_value={},
            ),
        ):
            assert feature.requires == set()

    def test_requires_secrets_when_socket_unavailable(self):
        """Falls back to requiring secrets when clusterd socket is unreachable."""
        feature = self._make_feature()
        with (
            patch(
                "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
                return_value=True,
            ),
            patch(
                "sunbeam.features.loadbalancer.feature.Client.from_socket",
                side_effect=Exception("socket not available"),
            ),
        ):
            reqs = feature.requires
        assert len(reqs) == 1
        assert next(iter(reqs)).name == "secrets"
