# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import os
import tarfile
from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import JujuException
from sunbeam.steps.horizon import AttachHorizonThemeStep, _validate_theme_path


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
def step_context():
    return Mock()


@pytest.fixture
def manifest_empty():
    """Manifest with no horizon config at all."""
    m = Mock()
    m.core.config.horizon = None
    return m


@pytest.fixture
def manifest_no_resource():
    """Manifest with a horizon section but no custom theme resource."""
    horizon_cfg = Mock()
    horizon_cfg.resources = Mock(custom_theme=None)
    m = Mock()
    m.core.config.horizon = horizon_cfg
    return m


@pytest.fixture
def manifest_with_theme(tmp_path):
    theme_file = _make_tar(tmp_path)
    horizon_cfg = Mock()
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


def _make_step(client, jhelper, manifest):
    return AttachHorizonThemeStep(
        client=client,
        jhelper=jhelper,
        manifest=manifest,
        model="openstack",
    )


def test_validate_theme_path_empty_is_ok():
    """Empty path is valid and means "no custom theme"."""
    assert _validate_theme_path("") is None


def test_validate_theme_path_valid_tarball(tmp_path):
    tar = _make_tar(tmp_path)
    assert _validate_theme_path(str(tar)) is None


def test_validate_theme_path_missing_file_raises():
    with pytest.raises(ValueError, match="does not exist"):
        _validate_theme_path("/nonexistent/theme.tar.gz")


def test_validate_theme_path_not_a_tarball_raises(tmp_path):
    bogus = tmp_path / "theme.tar.gz"
    bogus.write_text("definitely not a tarball")
    with pytest.raises(ValueError, match="not a valid tarball"):
        _validate_theme_path(str(bogus))


def test_run_with_manifest_theme_attaches(
    client,
    jhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    step_context,
):
    manifest, theme_file = manifest_with_theme
    step = _make_step(client, jhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    jhelper.attach_resource.assert_called_once_with(
        "horizon",
        "openstack",
        "custom-theme",
        str(theme_file),
    )


def test_run_with_stored_theme_attaches(
    client,
    jhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    step_context,
    tmp_path,
):
    theme_file = _make_tar(tmp_path)
    load_answers_patch.return_value = {"theme_path": str(theme_file)}
    step = _make_step(client, jhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    jhelper.attach_resource.assert_called_once_with(
        "horizon",
        "openstack",
        "custom-theme",
        str(theme_file),
    )


def test_run_no_theme_attaches_empty_sentinel(
    client,
    jhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    step_context,
):
    """No theme -> a real 0-byte tarball is attached as the sentinel."""
    captured = {}

    def _capture(*args, **kwargs):
        fp = args[3] if len(args) > 3 else kwargs["filepath"]
        captured["exists"] = os.path.isfile(fp)
        captured["size"] = os.path.getsize(fp)

    jhelper.attach_resource.side_effect = _capture

    step = _make_step(client, jhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    jhelper.attach_resource.assert_called_once()
    assert captured["exists"] is True
    assert captured["size"] == 0


def test_run_nonexistent_theme_path_fails(
    client,
    jhelper,
    manifest_empty,
    load_answers_patch,
    write_answers_patch,
    step_context,
):
    load_answers_patch.return_value = {"theme_path": "/nonexistent/theme.tar.gz"}
    step = _make_step(client, jhelper, manifest_empty)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    assert "invalid or missing" in result.message
    jhelper.attach_resource.assert_not_called()


def test_run_attach_resource_failure(
    client,
    jhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    step_context,
):
    manifest, _ = manifest_with_theme
    jhelper.attach_resource.side_effect = JujuException("juju is sad")
    step = _make_step(client, jhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.FAILED
    assert "Failed to attach resource" in result.message


def test_run_manifest_overrides_stored_answers(
    client,
    jhelper,
    manifest_with_theme,
    load_answers_patch,
    write_answers_patch,
    step_context,
    tmp_path,
):
    """Manifest theme path takes priority over a stored one on conflict."""
    manifest, theme_file = manifest_with_theme
    stale = _make_tar(tmp_path, name="stale.tar.gz")
    load_answers_patch.return_value = {"theme_path": str(stale)}

    step = _make_step(client, jhelper, manifest)
    result = step.run(step_context)

    assert result.result_type == ResultType.COMPLETED
    assert jhelper.attach_resource.call_args.args[3] == str(theme_file)


def test_prompt_writes_theme_path(
    client, jhelper, manifest_empty, load_answers_patch, write_answers_patch
):
    with patch("sunbeam.steps.horizon.QuestionBank") as qb:
        qb.return_value.theme_path.ask.return_value = "/some/theme.tar.gz"
        step = _make_step(client, jhelper, manifest_empty)
        step.prompt(console=Mock())

    write_answers_patch.assert_called_once()
    section, written = write_answers_patch.call_args.args[1:3]
    assert section == "Horizon"
    assert written["theme_path"] == "/some/theme.tar.gz"


def test_has_prompts_with_manifest_theme_is_false(client, jhelper, manifest_with_theme):
    manifest, _ = manifest_with_theme
    step = _make_step(client, jhelper, manifest)
    assert step.has_prompts() is False


def test_has_prompts_no_horizon_section_is_true(client, jhelper, manifest_empty):
    step = _make_step(client, jhelper, manifest_empty)
    assert step.has_prompts() is True


def test_has_prompts_horizon_without_resource_is_true(
    client, jhelper, manifest_no_resource
):
    step = _make_step(client, jhelper, manifest_no_resource)
    assert step.has_prompts() is True
