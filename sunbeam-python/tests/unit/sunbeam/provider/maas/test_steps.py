# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

import sunbeam.core.questions
import sunbeam.provider.maas.steps as maas_steps

from ...steps.test_configure import BaseTestUserQuestions


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.core.questions, "load_answers") as p:
        yield p


@pytest.fixture()
def write_answers():
    with patch.object(sunbeam.core.questions, "write_answers") as p:
        yield p


@pytest.fixture()
def question_bank():
    with patch.object(sunbeam.core.questions, "QuestionBank") as p:
        yield p


class TestMaasUserQuestions(BaseTestUserQuestions):
    __test__ = True

    @pytest.fixture(autouse=True)
    def setup_maas(self):
        self.maas_client = Mock()
        with patch("sunbeam.provider.maas.steps.maas_deployment") as p:
            self.maas_deployment = p
            yield

    def get_step(self):
        return maas_steps.MaasUserQuestions(self.cclient, self.maas_client)
