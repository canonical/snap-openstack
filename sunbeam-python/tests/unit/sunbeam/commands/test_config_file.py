# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.config_file import config_file
from sunbeam.core import config_overrides as co
from sunbeam.core.juju import ActionFailedException, JujuException

VALIDATE_OK = {
    "valid": "true",
    "errors": "[]",
    "warnings": "[]",
    "checks": "{}",
}


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def deployment():
    d = Mock()
    d.juju_controller = "controller"
    d.juju_account = Mock()
    return d


@pytest.fixture()
def client(deployment):
    return deployment.get_client.return_value


def _empty_store(client):
    client.cluster.get_config.side_effect = ConfigItemNotFoundException()


def _seed(client, overrides):
    client.cluster.get_config.side_effect = None
    client.cluster.get_config.return_value = json.dumps({"overrides": overrides})


def _written_overrides(client):
    key, value = client.cluster.update_config.call_args.args
    assert key == co.CONFIG_FILE_OVERRIDES_KEY
    return json.loads(value)["overrides"]


@pytest.fixture()
def preflight_patch():
    with patch("sunbeam.commands.config_file.run_preflight_checks") as p:
        yield p


@pytest.fixture()
def jhelper_patch():
    with patch("sunbeam.commands.config_file.JujuHelper") as p:
        helper = p.return_value
        helper.get_application_resources.return_value = [
            {"name": "config-override", "type": "file"},
            {"name": "policy-override", "type": "file"},
        ]
        helper.get_leader_unit.return_value = "glance/0"
        helper.run_action.return_value = dict(VALIDATE_OK)
        yield p


def _capture_attaches(helper):
    attached = {}

    def _capture(app, model, resource, path):
        attached[resource] = (Path(path).name, Path(path).read_text())

    helper.attach_resource.side_effect = _capture
    return attached


def _action_calls(helper):
    return [call.args for call in helper.run_action.call_args_list]


def test_add_validates_attaches_and_enables(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    """Adding an override stores it and, once valid, attaches and applies it."""
    _empty_store(client)
    config_file_ = tmp_path / "glance.conf"
    config_file_.write_text("[glance]\nfoo = bar\n")
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "add", "glance", str(config_file_)], obj=deployment
    )

    assert result.exit_code == 0, result.output
    record = _written_overrides(client)["glance"]
    assert record["config"] == "[glance]\nfoo = bar\n"
    assert record["policy"] is None
    assert record["sha256"] == co.content_sha256("[glance]\nfoo = bar\n", None)
    assert record["enabled"] is True
    assert attached["config-override"] == ("override.conf", "[glance]\nfoo = bar\n")
    assert attached["policy-override"] == ("override.yaml", "")
    assert _action_calls(helper) == [
        (
            "glance/0",
            co.OPENSTACK_MODEL,
            co.VALIDATE_OVERRIDES_ACTION,
            {"content": "[glance]\nfoo = bar\n"},
        ),
        ("glance/0", co.OPENSTACK_MODEL, co.APPLY_OVERRIDES_ACTION, {}),
    ]
    assert "Stored config-file override for glance" in result.output
    assert "Validation succeeded." in result.output
    assert "Enabled config-file override for glance." in result.output
    helper.wait_until_desired_status.assert_called_once()


def test_add_with_policy(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    _empty_store(client)
    config_file_ = tmp_path / "glance.conf"
    config_file_.write_text("[glance]\nfoo = bar\n")
    policy_file = tmp_path / "glance.policy.yaml"
    policy_file.write_text("get_image: role:admin\n")
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file,
        ["override", "add", "glance", str(config_file_), "--policy", str(policy_file)],
        obj=deployment,
    )

    assert result.exit_code == 0, result.output
    record = _written_overrides(client)["glance"]
    assert record["config"] == "[glance]\nfoo = bar\n"
    assert record["policy"] == "get_image: role:admin\n"
    assert record["enabled"] is True
    assert attached == {
        "config-override": ("override.conf", "[glance]\nfoo = bar\n"),
        "policy-override": ("override.yaml", "get_image: role:admin\n"),
    }
    assert [call.args[3] for call in helper.run_action.call_args_list] == [
        {"content": "[glance]\nfoo = bar\n"},
        {"content": "get_image: role:admin\n"},
        {},
    ]


def test_add_policy_only(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    _empty_store(client)
    policy_file = tmp_path / "glance.policy.yaml"
    policy_file.write_text("get_image: role:admin\n")
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file,
        ["override", "add", "glance", "--policy", str(policy_file)],
        obj=deployment,
    )

    assert result.exit_code == 0, result.output
    record = _written_overrides(client)["glance"]
    assert record["config"] is None
    assert record["policy"] == "get_image: role:admin\n"
    assert record["enabled"] is True
    assert attached["config-override"] == ("override.conf", "")
    assert attached["policy-override"] == ("override.yaml", "get_image: role:admin\n")


def test_get_output_write_failure_reported(
    runner, deployment, client, jhelper_patch, tmp_path
):
    """An unwritable --output is a clean error, not a traceback."""
    _seed(client, {"glance": co.make_record("[glance]\nfoo = bar\n", None)})

    result = runner.invoke(
        config_file,
        [
            "override",
            "get",
            "glance",
            "--output",
            str(tmp_path / "missing" / "glance.conf"),
        ],
        obj=deployment,
    )

    assert result.exit_code != 0
    assert "unable to write" in result.output


def test_add_without_config_or_policy_not_stored(runner, deployment, client):
    _empty_store(client)

    result = runner.invoke(config_file, ["override", "add", "glance"], obj=deployment)

    assert result.exit_code != 0
    assert "provide a configuration file" in result.output
    client.cluster.update_config.assert_not_called()


def test_add_app_not_deployed_not_stored(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    _empty_store(client)
    config_file_ = tmp_path / "glance.conf"
    config_file_.write_text("[glance]\nfoo = bar\n")
    helper = jhelper_patch.return_value
    helper.get_application_resources.side_effect = JujuException("gone")

    result = runner.invoke(
        config_file, ["override", "add", "glance", str(config_file_)], obj=deployment
    )

    assert result.exit_code != 0
    assert "not deployed" in result.output
    client.cluster.update_config.assert_not_called()
    helper.attach_resource.assert_not_called()


def test_add_policy_without_policy_resource_not_stored(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    _empty_store(client)
    config_file_ = tmp_path / "glance.conf"
    config_file_.write_text("[glance]\nfoo = bar\n")
    policy_file = tmp_path / "glance.policy.yaml"
    policy_file.write_text("get_image: role:admin\n")
    helper = jhelper_patch.return_value
    helper.get_application_resources.return_value = [
        {"name": "config-override", "type": "file"},
    ]

    result = runner.invoke(
        config_file,
        ["override", "add", "glance", str(config_file_), "--policy", str(policy_file)],
        obj=deployment,
    )

    assert result.exit_code != 0
    assert "policy-override" in result.output
    client.cluster.update_config.assert_not_called()
    helper.attach_resource.assert_not_called()


def test_add_validation_failure_stored_not_attached(
    runner, deployment, client, preflight_patch, jhelper_patch, tmp_path
):
    _empty_store(client)
    config_file_ = tmp_path / "glance.conf"
    config_file_.write_text("[glance]\nfoo = bar\n")
    helper = jhelper_patch.return_value
    helper.run_action.side_effect = ActionFailedException(
        "task error: Task 812: status 'failed', return code 0, "
        "details: Message: bad config",
        results={
            "valid": "false",
            "errors": '["bad config"]',
            "warnings": "[]",
            "checks": "{}",
        },
    )

    result = runner.invoke(
        config_file, ["override", "add", "glance", str(config_file_)], obj=deployment
    )

    assert result.exit_code != 0
    assert "bad config" in result.output
    assert "Validation failed" in result.output
    assert "stored but not applied" in result.output
    record = _written_overrides(client)["glance"]
    assert record["enabled"] is False
    helper.attach_resource.assert_not_called()


def test_enable_attaches_stored_content_and_applies(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", "get_image: role:admin\n")
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "enable", "glance"], obj=deployment
    )

    assert result.exit_code == 0, result.output
    assert attached == {
        "config-override": ("override.conf", "[glance]\nfoo = bar\n"),
        "policy-override": ("override.yaml", "get_image: role:admin\n"),
    }
    assert (
        "glance/0",
        co.OPENSTACK_MODEL,
        co.APPLY_OVERRIDES_ACTION,
        {},
    ) in _action_calls(helper)
    helper.wait_until_desired_status.assert_called_once()
    enabled = _written_overrides(client)["glance"]
    assert enabled["enabled"] is True
    assert enabled["config"] == "[glance]\nfoo = bar\n"


def test_enable_invalid_not_applied(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    helper.run_action.side_effect = ActionFailedException(
        "task error: Task 900: status 'failed', return code 0, "
        "details: Message: bad config",
        results={
            "valid": "false",
            "errors": '["bad config"]',
            "warnings": "[]",
            "checks": "{}",
        },
    )

    result = runner.invoke(
        config_file, ["override", "enable", "glance"], obj=deployment
    )

    assert result.exit_code != 0
    assert "bad config" in result.output
    assert "Validation failed" in result.output
    client.cluster.update_config.assert_not_called()


def test_enable_missing_record(runner, deployment, client):
    _empty_store(client)

    result = runner.invoke(
        config_file, ["override", "enable", "glance"], obj=deployment
    )

    assert result.exit_code != 0
    assert "no config-file override" in result.output


def test_disable_attaches_empty_and_keeps_content(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", "get_image: role:admin\n")
    record["enabled"] = True
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "disable", "glance"], obj=deployment
    )

    assert result.exit_code == 0, result.output
    assert attached == {
        "config-override": ("override.conf", ""),
        "policy-override": ("override.yaml", ""),
    }
    assert (
        "glance/0",
        co.OPENSTACK_MODEL,
        co.APPLY_OVERRIDES_ACTION,
        {},
    ) in _action_calls(helper)
    disabled = _written_overrides(client)["glance"]
    assert disabled["enabled"] is False
    assert disabled["config"] == record["config"]
    assert disabled["policy"] == record["policy"]


def test_disable_without_policy_resource(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    record["enabled"] = True
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    helper.get_application_resources.return_value = [
        {"name": "config-override", "type": "file"},
    ]
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "disable", "glance"], obj=deployment
    )

    assert result.exit_code == 0, result.output
    assert attached == {"config-override": ("override.conf", "")}


def test_remove_refuses_when_enabled(runner, deployment, client):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    record["enabled"] = True
    _seed(client, {"glance": record})

    result = runner.invoke(
        config_file, ["override", "remove", "glance"], obj=deployment
    )

    assert result.exit_code != 0
    assert "--force" in result.output
    client.cluster.update_config.assert_not_called()


def test_remove_force_clears_and_drops(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    record["enabled"] = True
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "remove", "glance", "--force"], obj=deployment
    )

    assert result.exit_code == 0, result.output
    assert attached == {
        "config-override": ("override.conf", ""),
        "policy-override": ("override.yaml", ""),
    }
    assert (
        "glance/0",
        co.OPENSTACK_MODEL,
        co.APPLY_OVERRIDES_ACTION,
        {},
    ) in _action_calls(helper)
    assert _written_overrides(client) == {}


def test_remove_disabled_without_force(
    runner, deployment, client, preflight_patch, jhelper_patch
):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    _seed(client, {"glance": record})
    helper = jhelper_patch.return_value
    attached = _capture_attaches(helper)

    result = runner.invoke(
        config_file, ["override", "remove", "glance"], obj=deployment
    )

    assert result.exit_code == 0, result.output
    assert attached["config-override"] == ("override.conf", "")
    assert _written_overrides(client) == {}


def test_get_writes_output_file(runner, deployment, client, tmp_path):
    record = co.make_record("[glance]\nfoo = bar\n", "policy: on\n")
    _seed(client, {"glance": record})
    output = tmp_path / "out.conf"

    result = runner.invoke(
        config_file,
        ["override", "get", "glance", "--output", str(output)],
        obj=deployment,
    )

    assert result.exit_code == 0, result.output
    assert output.read_text() == "[glance]\nfoo = bar\n"
    assert output.with_name("out.conf.policy.yaml").read_text() == "policy: on\n"


def test_get_prints_config(runner, deployment, client, jhelper_patch):
    record = co.make_record("[glance]\nfoo = bar\n", None)
    _seed(client, {"glance": record})
    with patch("sunbeam.commands.config_file.get_applied_overrides") as get_applied:
        get_applied.return_value = ["glance.conf"]
        result = runner.invoke(
            config_file, ["override", "get", "glance"], obj=deployment
        )

    assert result.exit_code == 0, result.output
    assert "[glance]" in result.output
    assert "foo = bar" in result.output


def test_get_prints_policy_only_record(runner, deployment, client, jhelper_patch):
    record = co.make_record(None, "policy: on\n")
    _seed(client, {"glance": record})
    with patch("sunbeam.commands.config_file.get_applied_overrides") as get_applied:
        get_applied.return_value = ["zz-config-overrides.yaml"]
        result = runner.invoke(
            config_file, ["override", "get", "glance"], obj=deployment
        )

    assert result.exit_code == 0, result.output
    assert "# --- policy ---" in result.output
    assert "policy: on" in result.output
    assert "None" not in result.output


def test_list_shows_records(runner, deployment, client):
    glance = co.make_record("[glance]\n", None)
    glance["enabled"] = True
    nova = co.make_record("[nova]\n", None)
    _seed(client, {"glance": glance, "nova": nova})

    result = runner.invoke(config_file, ["override", "list"], obj=deployment)

    assert result.exit_code == 0, result.output
    assert "glance" in result.output
    assert "nova" in result.output


def test_get_all_prints_every_override(runner, deployment, client):
    _seed(
        client,
        {
            "glance": co.make_record("[glance]\nfoo = bar\n", None),
            "nova": co.make_record("[nova]\nbaz = qux\n", None),
        },
    )

    result = runner.invoke(config_file, ["override", "get", "--all"], obj=deployment)

    assert result.exit_code == 0, result.output
    assert "# --- glance ---" in result.output
    assert "# --- nova ---" in result.output
    assert "foo = bar" in result.output


def test_get_all_writes_output_dir(runner, deployment, client, tmp_path):
    glance = co.make_record("[glance]\nfoo = bar\n", "policy: on\n")
    nova = co.make_record("[nova]\nbaz = qux\n", None)
    _seed(client, {"glance": glance, "nova": nova})

    output_dir = tmp_path / "collected"
    result = runner.invoke(
        config_file,
        ["override", "get", "--all", "--output-dir", str(output_dir)],
        obj=deployment,
    )

    assert result.exit_code == 0, result.output
    assert (output_dir / "glance.conf").read_text() == "[glance]\nfoo = bar\n"
    assert (output_dir / "glance.policy.yaml").read_text() == "policy: on\n"
    assert (output_dir / "nova.conf").read_text() == "[nova]\nbaz = qux\n"
    assert not (output_dir / "nova.policy.yaml").exists()
    assert "Collected 3 override(s)" in result.output


def test_get_usage_errors(runner, deployment, client):
    _seed(client, {"glance": co.make_record("[glance]\n", None)})

    both = runner.invoke(
        config_file, ["override", "get", "glance", "--all"], obj=deployment
    )
    assert both.exit_code != 0
    assert "not both" in both.output

    output_all = runner.invoke(
        config_file, ["override", "get", "--all", "--output", "x.conf"], obj=deployment
    )
    assert output_all.exit_code != 0
    assert "--output-dir" in output_all.output

    dir_without_all = runner.invoke(
        config_file, ["override", "get", "glance", "--output-dir", "x"], obj=deployment
    )
    assert dir_without_all.exit_code != 0
    assert "--all" in dir_without_all.output

    neither = runner.invoke(config_file, ["override", "get"], obj=deployment)
    assert neither.exit_code != 0
    assert "pass an application" in neither.output
