# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from sunbeam.commands.dashboard import (
    clear_theme,
    dashboard_url,
    retrieve_dashboard_url,
    set_theme,
)
from sunbeam.core import juju as juju_module
from sunbeam.core.common import PromptMode
from sunbeam.steps.horizon import THEME_CONFIG_SECTION


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def deployment():
    d = Mock()
    d.get_client.return_value = Mock()
    d.juju_controller = Mock()
    d.get_manifest.return_value = Mock()
    d.get_tfhelper.return_value = Mock()
    return d


@pytest.fixture
def jhelper_patch():
    with patch("sunbeam.commands.dashboard.JujuHelper") as p:
        yield p


@pytest.fixture
def run_plan_patch():
    with patch("sunbeam.commands.dashboard.run_plan") as p:
        yield p


@pytest.fixture
def attach_step_patch():
    with patch("sunbeam.commands.dashboard.AttachHorizonThemeStep") as p:
        yield p


@pytest.fixture
def write_answers_patch():
    with patch("sunbeam.commands.dashboard.write_answers") as p:
        yield p


def test_retrieve_dashboard_url_returns_url():
    jhelper = Mock()
    jhelper.get_leader_unit.return_value = "horizon/0"
    jhelper.run_action.return_value = {"url": "https://horizon.example.com"}
    assert retrieve_dashboard_url(jhelper) == "https://horizon.example.com"


def test_retrieve_dashboard_url_leader_not_found():
    jhelper = Mock()
    jhelper.get_leader_unit.side_effect = juju_module.LeaderNotFoundException(
        "no leader"
    )
    with pytest.raises(ValueError, match="Unable to get horizon leader"):
        retrieve_dashboard_url(jhelper)


def test_retrieve_dashboard_url_action_failed():
    jhelper = Mock()
    jhelper.get_leader_unit.return_value = "horizon/0"
    jhelper.run_action.side_effect = juju_module.ActionFailedException("nope")
    with pytest.raises(ValueError, match="Unable to retrieve URL"):
        retrieve_dashboard_url(jhelper)


def test_set_theme_runs_step_with_force_prompt(
    runner,
    deployment,
    jhelper_patch,
    run_plan_patch,
    attach_step_patch,
):
    result = runner.invoke(set_theme, obj=deployment)
    assert result.exit_code == 0
    attach_step_patch.assert_called_once()
    assert attach_step_patch.call_args.kwargs["prompt_mode"] == PromptMode.FORCE
    run_plan_patch.assert_called_once()


def test_clear_theme_writes_empty_path_and_runs_step(
    runner,
    deployment,
    jhelper_patch,
    run_plan_patch,
    attach_step_patch,
    write_answers_patch,
):
    result = runner.invoke(clear_theme, obj=deployment)
    assert result.exit_code == 0
    write_answers_patch.assert_called_once_with(
        deployment.get_client.return_value,
        THEME_CONFIG_SECTION,
        {"theme_path": ""},
    )
    assert attach_step_patch.call_args.kwargs["prompt_mode"] == PromptMode.NEVER


@patch("sunbeam.commands.dashboard.run_preflight_checks")
def test_dashboard_url_prints_url(preflight_patch, runner, deployment, jhelper_patch):
    with patch(
        "sunbeam.commands.dashboard.retrieve_dashboard_url",
        return_value="https://horizon.example.com",
    ):
        result = runner.invoke(dashboard_url, obj=deployment)
    assert result.exit_code == 0
    assert "horizon.example.com" in result.output
