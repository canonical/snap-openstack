# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import hashlib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core import config_overrides as co
from sunbeam.core.juju import ActionFailedException, JujuException, JujuWaitException


class FakeCluster:
    def __init__(self):
        self.store = {}

    def get_config(self, key):
        if key not in self.store:
            raise ConfigItemNotFoundException()
        return self.store[key]

    def update_config(self, key, value):
        self.store[key] = value


class FakeClient:
    def __init__(self):
        self.cluster = FakeCluster()


@pytest.fixture()
def client():
    return FakeClient()


SAMPLE_CONFIG = "[glance]\nfoo = bar\n"
SAMPLE_POLICY = "policy: enabled\n"


class TestLocalValidation:
    def test_validate_ini_ok(self):
        co.validate_ini("[DEFAULT]\na = 1\n\n[other]\nb = 2\n")

    def test_validate_ini_duplicate_section(self):
        with pytest.raises(co.ConfigOverridesError, match="duplicate section"):
            co.validate_ini("[a]\nx = 1\n[a]\ny = 2\n")

    def test_validate_ini_duplicate_option(self):
        with pytest.raises(co.ConfigOverridesError, match="duplicate option"):
            co.validate_ini("[a]\nx = 1\nx = 2\n")

    def test_validate_ini_missing_section_header(self):
        with pytest.raises(co.ConfigOverridesError, match="section header"):
            co.validate_ini("x = 1\n")

    def test_validate_policy_yaml_ok(self):
        co.validate_policy_yaml("policy: enabled\n")
        co.validate_policy_yaml("")

    def test_validate_policy_yaml_invalid(self):
        with pytest.raises(co.ConfigOverridesError, match="policy YAML"):
            co.validate_policy_yaml("a: [1\n")

    def test_validate_policy_yaml_not_a_mapping(self):
        with pytest.raises(co.ConfigOverridesError, match="mapping"):
            co.validate_policy_yaml("- one\n- two\n")


class TestContentSha:
    def test_stable_and_covers_both_halves(self):
        base = co.content_sha256(SAMPLE_CONFIG, SAMPLE_POLICY)
        assert base != co.content_sha256(SAMPLE_CONFIG, None)
        assert base != co.content_sha256(SAMPLE_CONFIG, SAMPLE_POLICY + "\n")

    def test_matches_manual_digest(self):
        digest = hashlib.sha256()
        digest.update(SAMPLE_CONFIG.encode())
        digest.update(b"\0")
        digest.update(SAMPLE_POLICY.encode())
        digest.update(b"\0")
        assert co.content_sha256(SAMPLE_CONFIG, SAMPLE_POLICY) == digest.hexdigest()


class TestStore:
    def test_read_overrides_missing(self, client):
        assert co.read_overrides(client) == {}

    def test_set_get_remove(self, client):
        record = co.make_record(SAMPLE_CONFIG, None)
        co.set_override(client, "glance", record)

        assert co.get_override(client, "glance") == record
        assert list(co.read_overrides(client)) == ["glance"]

        co.remove_override(client, "glance")
        assert co.read_overrides(client) == {}

    def test_get_missing_raises(self, client):
        with pytest.raises(co.ConfigOverridesError, match="no config-file override"):
            co.get_override(client, "glance")

    def test_remove_missing_raises(self, client):
        with pytest.raises(co.ConfigOverridesError, match="no config-file override"):
            co.remove_override(client, "glance")

    def test_make_record_policy_only(self):
        record = co.make_record(None, SAMPLE_POLICY)
        assert record["config"] is None
        assert record["policy"] == SAMPLE_POLICY


class TestValidationResult:
    def test_parse_validation_result(self):
        result = co.parse_validation_result(
            {
                "valid": "true",
                "errors": "[]",
                "warnings": '["w1"]',
                "checks": '{"ini": true}',
            }
        )
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == ["w1"]
        assert result.checks == {"ini": True}

    def test_parse_validation_result_invalid(self):
        result = co.parse_validation_result(
            {
                "valid": False,
                "errors": '["bad"]',
                "warnings": None,
                "checks": None,
            }
        )
        assert result.valid is False
        assert result.errors == ["bad"]
        assert result.warnings == []
        assert result.checks == {}


def _mock_jhelper():
    jhelper = Mock()
    jhelper.get_application_resources.return_value = [
        {"name": "config-override", "type": "file"},
    ]
    jhelper.get_leader_unit.return_value = "glance/0"
    jhelper.run_action.return_value = {
        "valid": "true",
        "errors": "[]",
        "warnings": "[]",
        "checks": "{}",
    }
    return jhelper


class TestValidateOverrides:
    def test_validate_overrides(self):
        jhelper = _mock_jhelper()

        result = co.validate_overrides(jhelper, "glance", "value")

        assert result.valid is True
        jhelper.get_application_resources.assert_called_once_with(
            "glance", co.OPENSTACK_MODEL
        )
        jhelper.get_leader_unit.assert_called_once_with("glance", co.OPENSTACK_MODEL)
        jhelper.run_action.assert_called_once_with(
            "glance/0",
            co.OPENSTACK_MODEL,
            "validate-config-overrides",
            {"content": "value"},
        )

    def test_validate_overrides_failed_action_is_a_verdict(self):
        """The charm fails the action for a rejected payload; keep the verdict."""
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = ActionFailedException(
            "task error: Task 812: status 'failed', return code 0, "
            "details: Message: not a known option",
            results={
                "valid": "false",
                "errors": '["DEFAULT/nope is not a known option"]',
                "warnings": "[]",
                "checks": '{"schema": "failed"}',
            },
        )

        result = co.validate_overrides(jhelper, "glance", "value")

        assert result.valid is False
        assert result.errors == ["DEFAULT/nope is not a known option"]
        assert result.checks == {"schema": "failed"}

    def test_validate_overrides_failed_action_without_verdict_raises(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = ActionFailedException(
            "task error: Task 812: status 'failed', return code 1, "
            "details: Message: exit status 1"
        )

        with pytest.raises(ActionFailedException, match="exit status 1"):
            co.validate_overrides(jhelper, "glance", "value")

    def test_validate_overrides_charm_unsupported(self):
        jhelper = _mock_jhelper()
        jhelper.get_application_resources.return_value = []

        with pytest.raises(co.ConfigOverridesError, match="config-override"):
            co.validate_overrides(jhelper, "glance", "value")
        jhelper.run_action.assert_not_called()

    def test_validate_overrides_app_not_deployed(self):
        jhelper = _mock_jhelper()
        jhelper.get_application_resources.side_effect = JujuException("gone")

        with pytest.raises(co.ConfigOverridesError, match="not deployed"):
            co.validate_overrides(jhelper, "glance", "value")
        jhelper.run_action.assert_not_called()

    def test_check_charm_support_names_missing_resource(self):
        jhelper = _mock_jhelper()

        with pytest.raises(co.ConfigOverridesError, match="policy-override"):
            co.check_charm_support(jhelper, "glance", policy=True)


class TestGetAppliedOverrides:
    def test_get_applied_overrides(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.return_value = {"applied": '["glance.conf"]'}

        applied = co.get_applied_overrides(jhelper, "glance")

        assert applied == ["glance.conf"]
        jhelper.run_action.assert_called_once_with(
            "glance/0", co.OPENSTACK_MODEL, "get-config-overrides", {}
        )

    def test_get_applied_overrides_without_action(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.return_value = {}

        assert co.get_applied_overrides(jhelper, "glance") == []


class TestApplyOverrides:
    def test_apply_overrides(self):
        jhelper = _mock_jhelper()

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is True
        jhelper.run_action.assert_called_once_with(
            "glance/0", co.OPENSTACK_MODEL, "apply-config-overrides", {}
        )
        jhelper.wait_until_desired_status.assert_not_called()

    def test_apply_overrides_verdict_from_failed_action_is_not_retried(self):
        """A rejected override arrives on the failed task and is final."""
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = ActionFailedException(
            "task error: Task 900: status 'failed', return code 0, "
            "details: Message: bad value",
            results={
                "valid": "false",
                "errors": '["bad value"]',
                "warnings": "[]",
                "checks": '{"schema": "failed"}',
            },
        )

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is False
        assert result.errors == ["bad value"]
        assert jhelper.run_action.call_count == 1

    def test_apply_overrides_retries_after_interrupted_task(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = [
            ActionFailedException(
                "task error: Task 670: status 'failed', return code -1, "
                "details: Message: terminated"
            ),
            {
                "valid": "true",
                "errors": "[]",
                "warnings": "[]",
                "checks": "{}",
            },
        ]

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is True
        assert jhelper.run_action.call_count == 2
        jhelper.wait_until_desired_status.assert_called_once_with(
            co.OPENSTACK_MODEL,
            ["glance"],
            status=["active", "blocked"],
            agent_status=["idle"],
            timeout=co.APPLY_SETTLE_TIMEOUT,
        )

    def test_apply_overrides_retries_even_if_settle_times_out(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = [
            ActionFailedException("task error: Message: terminated"),
            {
                "valid": "false",
                "errors": '["bad value"]',
                "warnings": "[]",
                "checks": "{}",
            },
        ]
        jhelper.wait_until_desired_status.side_effect = JujuWaitException("timeout")

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is False
        assert result.errors == ["bad value"]

    def test_apply_overrides_retries_up_to_apply_attempts(self):
        jhelper = _mock_jhelper()
        interrupted = ActionFailedException("task error: Message: terminated")
        jhelper.run_action.side_effect = [
            interrupted,
            interrupted,
            {
                "valid": "true",
                "errors": "[]",
                "warnings": "[]",
                "checks": "{}",
            },
        ]

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is True
        assert jhelper.run_action.call_count == co.APPLY_ATTEMPTS
        assert jhelper.wait_until_desired_status.call_count == co.APPLY_ATTEMPTS - 1

    def test_apply_overrides_retries_workload_container_restart(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = [
            ActionFailedException(
                "task error: Task 706: status 'failed', return code 1, "
                "details: Stderr: Uncaught ConnectionError in charm code: "
                "Could not connect to Pebble: socket not found at "
                "'/charm/containers/glance-api/pebble.socket' (container "
                "restarted?) Message: exit status 1"
            ),
            {
                "valid": "true",
                "errors": "[]",
                "warnings": "[]",
                "checks": "{}",
            },
        ]

        result = co.apply_overrides(jhelper, "glance")

        assert result.valid is True
        assert jhelper.run_action.call_count == 2
        jhelper.wait_until_desired_status.assert_called_once()

    def test_apply_overrides_raises_unknown_failure_verbatim(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = ActionFailedException(
            "task error: Task 671: status 'failed', return code 1, "
            "details: Message: exit status 1"
        )

        with pytest.raises(ActionFailedException) as excinfo:
            co.apply_overrides(jhelper, "glance")

        assert str(excinfo.value) == (
            "task error: Task 671: status 'failed', return code 1, "
            "details: Message: exit status 1"
        )
        assert jhelper.run_action.call_count == co.APPLY_ATTEMPTS
        assert jhelper.wait_until_desired_status.call_count == co.APPLY_ATTEMPTS - 1

    def test_apply_overrides_reports_restart_after_all_attempts(self):
        jhelper = _mock_jhelper()
        jhelper.run_action.side_effect = ActionFailedException(
            "task error: Task 682: status 'failed', return code -1, "
            "details: Message: terminated"
        )

        with pytest.raises(ActionFailedException) as excinfo:
            co.apply_overrides(jhelper, "glance")

        assert "unit was restarting" in str(excinfo.value)
        assert "config-file override get glance" in str(excinfo.value)
        assert "Message: terminated" in str(excinfo.value)
        assert jhelper.run_action.call_count == co.APPLY_ATTEMPTS
        assert jhelper.wait_until_desired_status.call_count == co.APPLY_ATTEMPTS - 1


class TestAttachOverrides:
    def _jhelper(self):
        jhelper = Mock()
        jhelper.get_application_resources.return_value = [
            {"name": "config-override", "type": "file"},
            {"name": "policy-override", "type": "file"},
        ]
        attached = {}

        def _capture(app, model, resource, path):
            attached[resource] = (Path(path).name, Path(path).read_text())

        jhelper.attach_resource.side_effect = _capture
        return jhelper, attached

    def test_policy_half_skipped_without_resource(self):
        jhelper, attached = self._jhelper()
        jhelper.get_application_resources.return_value = [
            {"name": "config-override", "type": "file"},
        ]

        co.attach_overrides(jhelper, "glance", SAMPLE_CONFIG, None)

        assert attached == {"config-override": ("override.conf", SAMPLE_CONFIG)}

    def test_config_resource_required(self):
        jhelper, attached = self._jhelper()
        jhelper.get_application_resources.return_value = []

        with pytest.raises(co.ConfigOverridesError, match="config-override"):
            co.attach_overrides(jhelper, "glance", SAMPLE_CONFIG, None)
        jhelper.attach_resource.assert_not_called()


class TestValidateEnabledOverrides:
    def _deployment(self, client):
        deployment = Mock()
        deployment.get_client.return_value = client
        deployment.juju_controller = "controller"
        return deployment

    def _seed(self, client, app, enabled, config=SAMPLE_CONFIG, policy=None):
        record = co.make_record(config, policy)
        record["enabled"] = enabled
        co.set_override(client, app, record)

    @patch("sunbeam.core.config_overrides.JujuHelper")
    @patch("sunbeam.core.config_overrides.validate_overrides")
    def test_skips_disabled_and_validates_enabled(self, validate, _jhelper, client):
        self._seed(client, "glance", enabled=False)
        self._seed(client, "keystone", enabled=True)
        validate.return_value = co.ValidationResult(valid=True)

        assert co.validate_enabled_overrides(self._deployment(client)) == []
        assert [call.args[1] for call in validate.call_args_list] == ["keystone"]

    @patch("sunbeam.core.config_overrides.JujuHelper")
    @patch("sunbeam.core.config_overrides.validate_overrides")
    def test_validates_each_stored_half(self, validate, _jhelper, client):
        self._seed(
            client,
            "glance",
            enabled=True,
            config=SAMPLE_CONFIG,
            policy=SAMPLE_POLICY,
        )
        validate.return_value = co.ValidationResult(valid=True)

        assert co.validate_enabled_overrides(self._deployment(client)) == []
        assert [call.args[2] for call in validate.call_args_list] == [
            SAMPLE_CONFIG,
            SAMPLE_POLICY,
        ]

    @patch("sunbeam.core.config_overrides.JujuHelper")
    @patch("sunbeam.core.config_overrides.validate_overrides")
    def test_reports_service_errors(self, validate, _jhelper, client):
        self._seed(client, "glance", enabled=True)
        validate.return_value = co.ValidationResult(
            valid=False, errors=["unknown policy rules: get_images"]
        )

        failures = co.validate_enabled_overrides(self._deployment(client))
        assert failures == [("glance", ["unknown policy rules: get_images"])]

    @patch("sunbeam.core.config_overrides.JujuHelper")
    @patch("sunbeam.core.config_overrides.validate_overrides")
    def test_reports_action_failures(self, validate, _jhelper, client):
        self._seed(client, "glance", enabled=True)
        validate.side_effect = co.ConfigOverridesError("no leader unit")

        failures = co.validate_enabled_overrides(self._deployment(client))
        assert failures == [("glance", ["no leader unit"])]

    @patch("sunbeam.core.config_overrides.JujuHelper")
    @patch("sunbeam.core.config_overrides.validate_overrides")
    def test_empty_store_passes(self, validate, _jhelper, client):
        assert co.validate_enabled_overrides(self._deployment(client)) == []
        validate.assert_not_called()
