# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

from sunbeam.core.common import ResultType, StepContext
from sunbeam.core.progress import NoOpReporter
from sunbeam.core.terraform import TerraformException
from sunbeam.features.loadbalancer.feature import (
    _AMPHORA_ENABLED_KEY,
    _AUTOCREATE_FLAVOR_KEY,
    _AUTOCREATE_IMAGE_KEY,
    _AUTOCREATE_NETWORK_KEY,
    _AUTOCREATE_SECGROUPS_KEY,
    _FLAVOR_KEY,
    _NETWORK_CIDR_KEY,
    _NETWORK_KEY,
    _SECGROUPS_KEY,
    _SUBNET_KEY,
    AmphoraConfigStep,
    CreateAmphoraResourcesStep,
    DeployAmphoraInfraStep,
    LoadbalancerAmphoraConfig,
    LoadbalancerFeatureConfig,
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
        bank.lb_mgmt_secgroup_ids.ask.return_value = resource_answers.get(
            _SECGROUPS_KEY, ""
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

    def test_secgroups_space_separated_to_list(self):
        """Space-separated secgroup IDs are split into a list."""
        result = self._run_prompt(
            stored_variables={},
            resource_answers={_SECGROUPS_KEY: "sg-1 sg-2 sg-3"},
        )
        assert result[_SECGROUPS_KEY] == ["sg-1", "sg-2", "sg-3"]

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
        assert written[_SECGROUPS_KEY] == ["tf-sg-mgmt", "tf-sg-health"]

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
        """NAD yaml and model are both passed in the same Terraform apply."""
        result, tfhelper, jhelper = self._run(ALL_VARIABLES)
        assert result.result_type == ResultType.COMPLETED
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert "multus-network-attachment-definitions" in override
        assert "model" in override
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
        from sunbeam.features.loadbalancer.feature import LoadbalancerFeature

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
        from sunbeam.features.interface.v1.base import FeatureRequirement

        feature = self._make_feature()
        with patch(
            "sunbeam.features.loadbalancer.feature.is_feature_gate_enabled",
            return_value=True,
        ):
            assert feature.requires == {FeatureRequirement("secrets")}


class TestLoadbalancerFeatureEnabledCommands:
    """Test that enabled_commands() always registers all amphora commands.

    The @feature_gate_command decorator is evaluated at class definition time
    (import time), so it handles hiding/erroring at invocation — the commands
    are unconditionally present in the enabled_commands() dict.
    """

    def _make_feature(self):
        from sunbeam.features.loadbalancer.feature import LoadbalancerFeature

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
        from sunbeam.features.loadbalancer.feature import LoadbalancerFeature

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
        import click
        import pytest

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
        import click
        import pytest

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
        import click
        import pytest

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
        from sunbeam.features.loadbalancer.feature import LoadbalancerFeature

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
