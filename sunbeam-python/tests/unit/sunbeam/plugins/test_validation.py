# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import click
import pytest

from sunbeam.plugins.validation import plugin as validation_plugin


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
        assert validation_plugin.validated_schedule(input_schedule) == input_schedule

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
            validation_plugin.validated_schedule(test_input)
        assert expected_msg in str(e)