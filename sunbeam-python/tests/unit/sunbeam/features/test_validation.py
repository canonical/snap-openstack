# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import click
import pytest

from sunbeam.features.validation import feature as validation_feature


class TestValidatorFunction:
    """Test validator functions."""

    @pytest.mark.parametrize(
        "input_schedule",
        [
            "",
            "5 4 * * *",
            "5 4 * * mon",
            "*/30 * * * *",
        ],
    )
    def test_valid_cron_expressions(self, input_schedule):
        """Verify valid cron expressions."""
        config = validation_feature.Config(schedule=input_schedule)
        assert config.schedule == input_schedule

    @pytest.mark.parametrize(
        "test_input,expected_msg",
        [
            ("*/5 * * * *", "Cannot schedule periodic check"),
            ("*/30 * * * * 6", "This cron does not support"),
            ("*/30 * *", "Exactly 5 columns must"),
            ("*/5 * * * xyz", "not acceptable"),
        ],
    )
    def test_invalid_cron_expressions(self, test_input, expected_msg):
        """Verify invalid cron expressions."""
        with pytest.raises(click.ClickException) as e:
            validation_feature.Config(schedule=test_input)
            assert expected_msg in str(e)

    @pytest.mark.parametrize(
        "test_args",
        [
            ["option_a 1"],
            ["option_b=1", "option_c 2"],
        ],
    )
    def test_parse_config_args_syntax_error(self, test_args):
        """Test if parse_config_args handles syntax error."""
        with pytest.raises(click.ClickException):
            validation_feature.parse_config_args(test_args)

    @pytest.mark.parametrize(
        "test_args",
        [
            (["option_a=1", "option_a=2", "option_b=3"]),
        ],
    )
    def test_parse_config_args_duplicated_params(self, test_args):
        """Test if parse_config_args handles duplicated parameters."""
        with pytest.raises(click.ClickException):
            validation_feature.parse_config_args(test_args)

    @pytest.mark.parametrize(
        "test_args,expected_output",
        [
            (["option_a=1"], {"option_a": "1"}),
            (["option_b = 2"], {"option_b ": " 2"}),
            (["option_a=1", "option_b = 2"], {"option_a": "1", "option_b ": " 2"}),
        ],
    )
    def test_valid_parse_config_args(self, test_args, expected_output):
        """Test if parse_config_args handles duplicated parameters."""
        output = validation_feature.parse_config_args(test_args)
        assert set(output.keys()) == set(expected_output.keys())
        for k, v in output.items():
            assert expected_output[k] == v

    @pytest.mark.parametrize(
        "input_args",
        [
            {"schedule": ""},
            {"schedule": "5 4 * * *"},
            {"schedule": "5 4 * * mon"},
            {"schedule": "*/30 * * * *"},
        ],
    )
    def test_valid_schedule_validated_config_args(self, input_args):
        """Test validated_config_args handles valid key correctly."""
        config = validation_feature.validated_config_args(input_args)
        assert config.schedule == input_args["schedule"]

    @pytest.mark.parametrize(
        "input_args",
        [
            {"schedule": "*/5 * * * *"},
            {"schedule": "*/30 * * * * 6"},
            {"schedule": "*/30 * *"},
            {"schedule": "*/5 * * * xyz"},
        ],
    )
    def test_invalid_schedule_validated_config_args(self, input_args):
        """Test validated_config_args handles valid key but invalid value correctly."""
        # This is raise by `validated_schedule`
        with pytest.raises(click.ClickException):
            validation_feature.validated_config_args(input_args)

    @pytest.mark.parametrize(
        "input_args",
        [
            {"schedules": "*/5 * * * *"},  # e.g. typo
            {"scehdule": "*/30 * * * * 6"},  # e.g. typo
        ],
    )
    def test_invalid_key_validated_config_args(self, input_args):
        """Test validated_config_args handles invalid key correctly."""
        # This is raise by `validated_config_args`
        with pytest.raises(click.ClickException):
            validation_feature.validated_config_args(input_args)

    def test_get_enabled_roles_all(self):
        deployment = MagicMock()
        client = MagicMock()
        deployment.get_client.return_value = client
        client.cluster.list_nodes_by_role.side_effect = [
            ["compute"],
            ["control"],
            ["storage"],
            ["network"],
        ]
        roles = validation_feature.get_enabled_roles(deployment)
        assert set(roles.split(",")) == {"compute", "control", "storage", "network"}

    def test_get_enabled_roles_some(self):
        deployment = MagicMock()
        client = MagicMock()
        deployment.get_client.return_value = client
        client.cluster.list_nodes_by_role.side_effect = [
            ["compute"],
            ["control"],
            [],
            [],
        ]
        roles = validation_feature.get_enabled_roles(deployment)
        assert set(roles.split(",")) == {"compute", "control"}
