# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.features.interface.v1.openstack import TerraformPlanLocation
from sunbeam.features.ldap.feature import (
    AddLDAPDomainStep,
    DisableLDAPDomainStep,
    LDAPFeature,
    UpdateLDAPDomainStep,
)


@pytest.fixture()
def read_config():
    with patch("sunbeam.features.ldap.feature.read_config") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.features.ldap.feature.update_config") as p:
        yield p


@pytest.fixture()
def ssnap():
    with patch("sunbeam.clusterd.service.Snap") as p:
        yield p


class FakeLDAPFeature(LDAPFeature):
    def __init__(self):
        self.config_flags = None
        self.name = "ldap"
        self.app_name = self.name.capitalize()
        self.tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO
        self.tfplan = "fake-plan"
        self._manifest = Mock()
        self.deployment = Mock()


class TestAddLDAPDomainStep:
    def setup_method(self):
        self.jhelper = Mock()
        self.charm_config = {"domain-name": "dom1"}
        self.feature = FakeLDAPFeature()

    def test_is_skip(self):
        step = AddLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, {})
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        step = AddLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, {})
        assert not step.has_prompts()

    def test_enable_first_domain(self, read_config, update_config, snap):
        read_config.return_value = {}
        step = AddLDAPDomainStep(
            Mock(), Mock(), self.jhelper, self.feature, self.charm_config
        )
        result = step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_second_domain(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = AddLDAPDomainStep(
            Mock(), Mock(), self.jhelper, self.feature, {"domain-name": "dom2"}
        )
        result = step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-apps": {
                    "dom1": {"domain-name": "dom1"},
                    "dom2": {"domain-name": "dom2"},
                },
            }
        )
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.COMPLETED

    def test_enable_tf_apply_failed(self, read_config, update_config, snap):
        read_config.return_value = {}
        step = AddLDAPDomainStep(
            Mock(), Mock(), self.jhelper, self.feature, self.charm_config
        )
        step.tfhelper.apply.side_effect = TerraformException("apply failed...")
        result = step.run()
        step.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_enable_waiting_timed_out(self, read_config, update_config, snap):
        self.jhelper.wait_until_active.side_effect = TimeoutError("timed out")
        read_config.return_value = {}
        step = AddLDAPDomainStep(
            Mock(), Mock(), self.jhelper, self.feature, self.charm_config
        )
        result = step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDisableLDAPDomainStep:
    def setup_method(self):
        self.jhelper = Mock()
        self.charm_config = {"domain-name": "dom1"}
        self.feature = FakeLDAPFeature()

    def test_is_skip(self):
        step = DisableLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, "dom1")
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        step = DisableLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, "dom1")
        assert not step.has_prompts()

    def test_disable(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, "dom1")
        step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {"ldap-channel": "2023.2/edge", "ldap-apps": {}}
        )
        step.tfhelper.apply.assert_called_once_with()

    def test_disable_tf_apply_failed(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, "dom1")
        step.tfhelper.apply.side_effect = TerraformException("apply failed...")
        result = step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {"ldap-channel": "2023.2/edge", "ldap-apps": {}}
        )
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_disable_wrong_domain(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = DisableLDAPDomainStep(Mock(), Mock(), self.jhelper, self.feature, "dom2")
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Domain not found"


class TestUpdateLDAPDomainStep:
    def setup_method(self):
        self.jhelper = Mock()
        self.charm_config = {"domain-name": "dom1"}
        self.feature = FakeLDAPFeature()

    def test_is_skip(self):
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, self.charm_config
        )
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_has_prompts(self):
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, self.charm_config
        )
        assert not step.has_prompts()

    def test_update_domain(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, self.charm_config
        )
        result = step.run()
        step.tfhelper.write_tfvars.assert_called_with(
            {
                "ldap-channel": "2023.2/edge",
                "ldap-apps": {"dom1": {"domain-name": "dom1"}},
            }
        )
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.COMPLETED

    def test_update_wrong_domain(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, {"domain-name": "dom2"}
        )
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Domain not found"

    def test_tf_apply_failed(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, self.charm_config
        )
        step.tfhelper.apply.side_effect = TerraformException("apply failed...")
        result = step.run()
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_update_waiting_timed_out(self, read_config, update_config, snap):
        read_config.return_value = {
            "ldap-channel": "2023.2/edge",
            "ldap-apps": {"dom1": {"domain-name": "dom1"}},
        }
        step = UpdateLDAPDomainStep(
            Mock(), self.jhelper, self.feature, self.charm_config
        )
        self.jhelper.wait_until_active.side_effect = TimeoutError("timed out")
        step.tfhelper.apply.side_effect = TerraformException("apply failed...")
        result = step.run()
        step.tfhelper.apply.assert_called_once_with()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."
