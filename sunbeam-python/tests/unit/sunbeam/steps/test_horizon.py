# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import tarfile
from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import PromptMode, ResultType
from sunbeam.core.juju import JujuException
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.horizon import AttachHorizonThemeStep


def _make_tar(tmp_path, name="theme.tar.gz"):
    """Create a real tarball so tarfile.is_tarfile() succeeds."""
    inner = tmp_path / "dummy.txt"
    inner.write_text("hello")
    tar_path = tmp_path / name
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(inner, arcname="dummy.txt")
    return tar_path


@pytest.fixture
def client():
    return Mock()


@pytest.fixture
def jhelper():
    return Mock()


@pytest.fixture
def tfhelper():
    return Mock()


@pytest.fixture
def manifest_empty():
    m = Mock()
    m.core.config.horizon = None
    return m


@pytest.fixture
def manifest_with_theme(tmp_path):
    theme_file = _make_tar(tmp_path)
    horizon_cfg = Mock()
    horizon_cfg.dict.return_value = {
        "enable_custom_theme": True,
        "custom_theme_name": "test-theme",
        "default_theme": "test-theme",
        "disable_default_themes": False,
        "disable_ubuntu_theme": False,
    }
    horizon_cfg.resources = Mock(custom_theme=theme_file)
    m = Mock()
    m.core.config.horizon = horizon_cfg
    return m, theme_file


@pytest.fixture
def load_answers_patch():
    with patch("sunbeam.steps.horizon.load_answers") as p:
        p.return_value = {}
        yield p


@pytest.fixture
def write_answers_patch():
    with patch("sunbeam.steps.horizon.write_answers") as p:
        yield p


@pytest.fixture
def read_config_patch():
    """Patch read_config so the merge step gets a real dict."""
    with patch("sunbeam.steps.horizon.read_config") as p:
        p.return_value = {}
        yield p


def _make_step(client, jhelper, tfhelper, manifest, prompt_mode=PromptMode.AUTO):
    return AttachHorizonThemeStep(
        client=client,
        jhelper=jhelper,
        tfhelper=tfhelper,
        manifest=manifest,
        model="openstack",
        prompt_mode=prompt_mode,
    )


def test_run_no_custom_theme_applies_defaults(
    client,
    jhelper,
    tfhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    step = _make_step(client, jhelper, tfhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    jhelper.attach_resource.assert_not_called()
    override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs["override_tfvars"]
    assert override["horizon-config"]["include-default-themes"] is True
    assert override["horizon-config"]["include-ubuntu-theme"] is True
    assert override["horizon-config"]["default-theme"] == "ubuntu"


def test_run_with_manifest_theme_attaches_and_applies(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    manifest, theme_file = manifest_with_theme
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    jhelper.attach_resource.assert_called_once_with(
        application="horizon",
        model="openstack",
        resource="custom-theme",
        filepath=str(theme_file),
    )
    override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs["override_tfvars"]
    assert override["horizon-config"]["custom-theme-name"] == "test-theme"
    assert override["horizon-config"]["default-theme"] == "test-theme"


def test_run_missing_theme_path_fails(
    client,
    jhelper,
    tfhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    load_answers_patch.return_value = {"enable_custom_theme": True}
    step = _make_step(client, jhelper, tfhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    jhelper.attach_resource.assert_not_called()


def test_run_nonexistent_theme_path_fails(
    client,
    jhelper,
    tfhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    load_answers_patch.return_value = {
        "enable_custom_theme": True,
        "theme_path": "/nonexistent/theme.tar.gz",
    }
    step = _make_step(client, jhelper, tfhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    assert "invalid or missing" in result.message


def test_run_attach_resource_failure(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    manifest, _ = manifest_with_theme
    jhelper.attach_resource.side_effect = JujuException("juju is sad")
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    assert "failed to attach resource" in result.message
    tfhelper.update_tfvars_and_apply_tf.assert_not_called()


def test_run_terraform_failure(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    manifest, _ = manifest_with_theme
    tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException("boom")
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    assert "failed to update tfvars" in result.message


def test_run_read_config_not_found_uses_empty_dict(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    manifest, _ = manifest_with_theme
    read_config_patch.side_effect = ConfigItemNotFoundException("none")
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED


def test_run_merges_with_existing_tfvars(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    """Existing horizon-config values are preserved, new ones override."""
    manifest, _ = manifest_with_theme
    read_config_patch.return_value = {
        "horizon-config": {
            "debug": "true",
            "custom-theme-name": "old-theme",
        },
    }
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs["override_tfvars"]
    # Preserved
    assert override["horizon-config"]["debug"] == "true"
    # Overridden
    assert override["horizon-config"]["custom-theme-name"] == "test-theme"


def test_has_prompts_force_mode(client, jhelper, tfhelper, manifest_empty):
    step = _make_step(
        client, jhelper, tfhelper, manifest_empty, prompt_mode=PromptMode.FORCE
    )
    assert step.has_prompts() is True


def test_has_prompts_never_mode(client, jhelper, tfhelper, manifest_empty):
    step = _make_step(
        client, jhelper, tfhelper, manifest_empty, prompt_mode=PromptMode.NEVER
    )
    assert step.has_prompts() is False


def test_has_prompts_auto_with_manifest(
    client, jhelper, tfhelper, manifest_with_theme, load_answers_patch
):
    manifest, _ = manifest_with_theme
    step = _make_step(client, jhelper, tfhelper, manifest)
    assert step.has_prompts() is False


def test_has_prompts_auto_with_stored(
    client, jhelper, tfhelper, manifest_empty, load_answers_patch
):
    load_answers_patch.return_value = {"enable_custom_theme": False}
    step = _make_step(client, jhelper, tfhelper, manifest_empty)
    assert step.has_prompts() is False


def test_has_prompts_auto_no_data(
    client, jhelper, tfhelper, manifest_empty, load_answers_patch
):
    step = _make_step(client, jhelper, tfhelper, manifest_empty)
    assert step.has_prompts() is True


def test_manifest_overrides_stored_answers(
    client,
    jhelper,
    tfhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    read_config_patch,
    step_context,
):
    """Manifest values take priority over stored answers on conflict."""
    manifest, _ = manifest_with_theme
    load_answers_patch.return_value = {
        "enable_custom_theme": True,
        "custom_theme_name": "stale-theme",
        "default_theme": "stale-theme",
    }
    step = _make_step(client, jhelper, tfhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs["override_tfvars"]
    assert override["horizon-config"]["custom-theme-name"] == "test-theme"
