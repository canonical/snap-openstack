# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.upgrades.inter_channel import BaseUpgrade


class TestBaseUpgrade:
    def setup_method(self):
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()

    def test_upgrade_applications(self):
        model = "openstack"
        apps = ["nova"]
        charms = ["nova-k8s"]
        config = "openstackterraformvar"
        timeout = 60

        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.manifest,
            model,
        )

        result = upgrader.upgrade_applications(
            apps, charms, model, self.tfhelper, config, timeout
        )
        self.tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once_with(
            self.client, self.manifest, charms, config
        )
        self.jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_upgrade_applications_tf_failed(self):
        self.tfhelper.update_partial_tfvars_and_apply_tf.side_effect = (
            TerraformException("apply failed...")
        )

        model = "openstack"
        apps = ["nova"]
        charms = ["nova-k8s"]
        config = "openstackterraformvar"
        timeout = 60

        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.manifest,
            model,
        )

        result = upgrader.upgrade_applications(
            apps, charms, model, self.tfhelper, config, timeout
        )
        self.tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once_with(
            self.client, self.manifest, charms, config
        )
        self.jhelper.wait_until_desired_status.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_upgrade_applications_waiting_timed_out(self):
        self.jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        model = "openstack"
        apps = ["nova"]
        charms = ["nova-k8s"]
        config = "openstackterraformvar"
        timeout = 60

        upgrader = BaseUpgrade(
            "test name",
            "test description",
            self.client,
            self.jhelper,
            self.manifest,
            model,
        )

        result = upgrader.upgrade_applications(
            apps, charms, model, self.tfhelper, config, timeout
        )
        self.tfhelper.update_partial_tfvars_and_apply_tf.assert_called_once_with(
            self.client, self.manifest, charms, config
        )
        self.jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
