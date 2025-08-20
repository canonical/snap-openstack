# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.features.pro.feature import (
    DisableUbuntuProApplicationStep,
    EnableUbuntuProApplicationStep,
)


class TestEnableUbuntuProApplicationStep(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.model = "test-model"
        self.token = "TOKENFORTESTING"
        self.step = EnableUbuntuProApplicationStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.token,
            self.model,
        )

    def test_is_skip(self):
        result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        assert not self.step.has_prompts()

    def test_enable(self):
        result = self.step.run()
        self.tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            self.client,
            self.manifest,
            tfvar_config=None,
            override_tfvars={"machine-model": self.model, "token": self.token},
        )
        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_tf_apply_failed(self):
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = self.step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_enable_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        result = self.step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDisableUbuntuProApplicationStep(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.step = DisableUbuntuProApplicationStep(
            self.client, self.tfhelper, self.manifest
        )

    def test_is_skip(self):
        result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        assert not self.step.has_prompts()

    def test_disable(self):
        result = self.step.run()
        self.tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            self.client,
            self.manifest,
            tfvar_config=None,
            override_tfvars={"token": ""},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_disable_tf_apply_failed(self):
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = self.step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."
