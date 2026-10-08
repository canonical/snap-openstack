# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from unittest.mock import call as mock_call

import click
import pytest
from jubilant.statustypes import AppStatus, AppStatusRelation

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformException
from sunbeam.features.loadbalancer import feature as loadbalancer_feature
from sunbeam.features.observability import feature as observability_feature


@pytest.fixture()
def observabilityfeature():
    with patch("sunbeam.features.observability.feature.ObservabilityFeature") as p:
        yield p


@pytest.fixture()
def ssnap():
    with patch("sunbeam.core.k8s.Snap") as p:
        yield p


@pytest.fixture()
def update_config():
    with patch("sunbeam.features.observability.feature.update_config") as p:
        yield p


@pytest.fixture()
def read_config_obs():
    with patch("sunbeam.features.observability.feature.read_config") as p:
        yield p


@pytest.fixture()
def k8shelper():
    with patch("sunbeam.features.observability.feature.K8SHelper") as p:
        p.get_default_storageclass.return_value = "csi-rawfile-default"
        yield p


@pytest.fixture()
def run_plan_obs():
    with patch("sunbeam.features.observability.feature.run_plan") as p:
        yield p


@pytest.fixture()
def juju_helper_obs():
    with patch("sunbeam.features.observability.feature.JujuHelper") as p:
        yield p


class TestDeployObservabilityStackStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        ssnap,
        read_config_obs,
        update_config,
        k8shelper,
        step_context,
    ):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        jhelper.get_application_names.return_value = ["app1", "app2", "app3"]
        read_config_obs.side_effect = ConfigItemNotFoundException("not found")
        observabilityfeature.name = "observability.embedded"
        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        ssnap,
        read_config_obs,
        update_config,
        k8shelper,
        step_context,
    ):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        read_config_obs.side_effect = ConfigItemNotFoundException("not found")
        observabilityfeature.name = "observability.embedded"
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        ssnap,
        read_config_obs,
        update_config,
        k8shelper,
        step_context,
    ):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        jhelper.get_application_names.return_value = ["app1", "app2", "app3"]
        jhelper.wait_until_active.side_effect = TimeoutError("timed out")
        read_config_obs.side_effect = ConfigItemNotFoundException("not found")
        observabilityfeature.name = "observability.embedded"

        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_is_skip_no_modification(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap, step_context
    ):
        ssnap().config.get.return_value = "k8s"
        with patch(
            "sunbeam.features.observability.feature"
            ".check_storage_modifications_in_manifest",
            return_value=[],
        ):
            step = observability_feature.DeployObservabilityStackStep(
                deployment, observabilityfeature, tfhelper, jhelper
            )
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_modification_detected(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap, step_context
    ):
        ssnap().config.get.return_value = "k8s"
        with patch(
            "sunbeam.features.observability.feature"
            ".check_storage_modifications_in_manifest",
            return_value=["prometheus-storage"],
        ):
            step = observability_feature.DeployObservabilityStackStep(
                deployment, observabilityfeature, tfhelper, jhelper
            )
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "immutable" in result.message


class TestUpdateObservabilityModelConfigStep:
    """Test the UpdateObservabilityModelConfigStep."""

    def test_is_skip_no_modification(
        self, deployment, tfhelper, observabilityfeature, ssnap, step_context
    ):
        ssnap().config.get.return_value = "k8s"
        with patch(
            "sunbeam.features.observability.feature"
            ".check_storage_modifications_in_manifest",
            return_value=[],
        ):
            step = observability_feature.UpdateObservabilityModelConfigStep(
                deployment, observabilityfeature, tfhelper
            )
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_modification_detected(
        self, deployment, tfhelper, observabilityfeature, ssnap, step_context
    ):
        ssnap().config.get.return_value = "k8s"
        with patch(
            "sunbeam.features.observability.feature"
            ".check_storage_modifications_in_manifest",
            return_value=["prometheus-storage"],
        ):
            step = observability_feature.UpdateObservabilityModelConfigStep(
                deployment, observabilityfeature, tfhelper
            )
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "immutable" in result.message


class TestRemoveObservabilityStackStep:
    def test_run(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap, step_context
    ):
        ssnap().config.get.return_value = "k8s"
        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        ssnap,
        step_context,
    ):
        ssnap().config.get.return_value = "k8s"
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        ssnap,
        step_context,
    ):
        ssnap().config.get.return_value = "k8s"
        jhelper.wait_model_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAttachHardwareObserverResourceStep:
    def test_run(self, deployment, jhelper, step_context):
        """Happy path: attach_resource called with correct args."""
        step = observability_feature.AttachHardwareObserverResourceStep(
            deployment, jhelper, "firmware", "/tmp/firmware.bin"
        )
        result = step.run(step_context)

        jhelper.attach_resource.assert_called_once_with(
            observability_feature.HARDWARE_OBSERVER_APP,
            deployment.openstack_machines_model,
            "firmware",
            "/tmp/firmware.bin",
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_attach_failed(self, deployment, jhelper, step_context):
        """Exception from attach_resource returns FAILED."""
        jhelper.attach_resource.side_effect = Exception("attach failed")

        step = observability_feature.AttachHardwareObserverResourceStep(
            deployment, jhelper, "firmware", "/tmp/firmware.bin"
        )
        result = step.run(step_context)

        jhelper.attach_resource.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "attach failed"


class TestListHardwareObserverResourcesStep:
    def test_run(self, deployment, jhelper, step_context):
        """Happy path: returns JSON-encoded list of resource dicts sorted by name."""
        resources = [
            {"name": "firmware", "type": "file", "description": "Firmware binary"},
            {"name": "storcli-amd64", "type": "file", "description": "StorCLI tool"},
        ]
        jhelper.get_application_resources.return_value = resources

        step = observability_feature.ListHardwareObserverResourcesStep(
            deployment, jhelper
        )
        result = step.run(step_context)

        jhelper.get_application_resources.assert_called_once_with(
            observability_feature.HARDWARE_OBSERVER_APP,
            deployment.openstack_machines_model,
        )
        assert result.result_type == ResultType.COMPLETED
        assert json.loads(result.message) == resources

    def test_run_failed(self, deployment, jhelper, step_context):
        """Exception from get_application_resources returns FAILED."""
        jhelper.get_application_resources.side_effect = Exception("list failed")

        step = observability_feature.ListHardwareObserverResourcesStep(
            deployment, jhelper
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == "list failed"


class TestAttachResourceCommand:
    """Tests for the attach_resource CLI command (resource name validation)."""

    def _resources_json(self, names):
        return json.dumps(
            [{"name": n, "type": "file", "description": ""} for n in names]
        )

    def _make_feature(self):
        return observability_feature.EmbeddedObservabilityFeature.__new__(
            observability_feature.EmbeddedObservabilityFeature
        )

    def _call_attach(self, feature, deployment, resource_name, resource_path):
        """Invoke attach_resource callback with an active Click context."""
        cmd = observability_feature.ObservabilityFeature.attach_resource
        with click.Context(cmd, obj=deployment):
            return feature.attach_resource.callback(
                feature, resource_name, resource_path
            )

    def test_invalid_resource_name_raises(
        self, deployment, run_plan_obs, juju_helper_obs
    ):
        """attach_resource raises ClickException when name is not in the list."""
        with patch(
            "sunbeam.features.observability.feature.get_step_message",
            return_value=self._resources_json(["firmware", "storcli-amd64"]),
        ):
            with pytest.raises(click.ClickException) as exc_info:
                self._call_attach(
                    self._make_feature(), deployment, "bad-resource", "/tmp/file.bin"
                )

        msg = exc_info.value.format_message()
        assert "bad-resource" in msg
        assert "hardware-monitoring-utility list" in msg

    def test_valid_resource_name_proceeds(
        self, deployment, run_plan_obs, juju_helper_obs
    ):
        """attach_resource proceeds to attach when name is valid."""
        with patch(
            "sunbeam.features.observability.feature.get_step_message",
            return_value=self._resources_json(["firmware"]),
        ):
            self._call_attach(
                self._make_feature(), deployment, "firmware", "/tmp/file.bin"
            )

        # Two run_plan calls: one for list, one for attach
        assert run_plan_obs.call_count == 2


class TestDeployObservabilityAgentStep:
    def test_run(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        step = observability_feature.DeployObservabilityAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_includes_microovn_when_present(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Microovn is added to integration-apps when it exists in the model."""
        jhelper.get_model_status.return_value = Mock(
            apps={
                "openstack-hypervisor": Mock(),
                "microovn": Mock(),
            }
        )

        step = observability_feature.DeployObservabilityAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        step.run(step_context)

        call_kwargs = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs
        integration_apps = call_kwargs["override_tfvars"][
            "observability-agent-integration-apps"
        ]
        assert "microovn" in integration_apps

    def test_run_excludes_microovn_when_absent(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Microovn is not added when it does not exist in the model."""
        jhelper.get_model_status.return_value = Mock(
            apps={
                "openstack-hypervisor": Mock(),
            }
        )

        step = observability_feature.DeployObservabilityAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        step.run(step_context)

        call_kwargs = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs
        integration_apps = call_kwargs["override_tfvars"][
            "observability-agent-integration-apps"
        ]
        assert "microovn" not in integration_apps

    def test_run_tf_apply_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployObservabilityAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = observability_feature.DeployObservabilityAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveObservabilityAgentStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        update_config,
        step_context,
    ):
        step = observability_feature.RemoveObservabilityAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveObservabilityAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveObservabilityAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDeployObservabilityAgentInfraStep:
    def test_run(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        deployment.infra_model = "openstack-infra"
        step = observability_feature.DeployObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_uses_sunbeam_clusterd_for_juju_info(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """sunbeam-clusterd is the juju-info principal, cos-agent list is empty.

        Also asserts that tls_insecure_skip_verify is NOT set for the infra model.
        """
        deployment.infra_model = "openstack-infra"
        step = observability_feature.DeployObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        step.run(step_context)

        call_kwargs = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs
        override_tfvars = call_kwargs["override_tfvars"]
        assert override_tfvars["observability-agent-integration-apps"] == []
        assert override_tfvars["observability-agent-integration-apps-juju-info"] == [
            observability_feature.SUNBEAM_CLUSTERD_APP
        ]
        assert "opentelemetry-collector-config" not in override_tfvars

    def test_run_tf_apply_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        deployment.infra_model = "openstack-infra"
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        deployment.infra_model = "openstack-infra"
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = observability_feature.DeployObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveObservabilityAgentInfraStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        update_config,
        step_context,
    ):
        deployment.infra_model = "openstack-infra"
        step = observability_feature.RemoveObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        deployment.infra_model = "openstack-infra"
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        step_context,
    ):
        deployment.infra_model = "openstack-infra"
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveObservabilityAgentInfraStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


@pytest.fixture()
def remote_cos_model_names(jhelper):
    jhelper.get_model_name_with_owner.side_effect = lambda model: f"admin/{model}"


@pytest.mark.usefixtures("remote_cos_model_names")
class TestIntegrateRemoteCosOffersStep:
    @pytest.mark.parametrize("maas", [False, True])
    @pytest.mark.parametrize("loki_offer", ["", "remotecos:admin/loki"])
    def test_run_integrates_all_collectors(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        run,
        step_context,
        maas,
        loki_offer,
    ):
        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = loki_offer
        deployment.openstack_machines_model = "openstack-machines"
        deployment.infra_model = "openstack-infra"
        targets = [
            ("openstack", "opentelemetry-collector"),
            ("openstack", "opentelemetry-collector-infra"),
            ("openstack-machines", "opentelemetry-collector"),
        ]
        if maas:
            targets.append(("openstack-infra", "opentelemetry-collector"))
        offers = [
            ("grafana-dashboards-provider", "remotecos:admin/grafana"),
            ("send-remote-write", "remotecos:admin/prometheus"),
        ]
        if loki_offer:
            offers.append(("send-loki-logs", loki_offer))

        with (
            patch(
                "sunbeam.features.observability.feature.is_maas_deployment",
                return_value=maas,
            ),
            patch.object(
                observability_feature.IntegrateRemoteCosOffersStep,
                "_get_juju_binary",
                return_value="juju",
            ),
        ):
            step = observability_feature.IntegrateRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        assert [call.args[0] for call in run.call_args_list] == [
            ["juju", "integrate", "-m", f"admin/{model}", f"{app}:{endpoint}", offer]
            for model, app in targets
            for endpoint, offer in offers
        ]
        assert jhelper.wait_application_ready.call_args_list == [
            mock_call(
                app, model, timeout=observability_feature.OBSERVABILITY_DEPLOY_TIMEOUT
            )
            for model, app in targets
        ]
        assert result.result_type == ResultType.COMPLETED

    def test_run(
        self, deployment, jhelper, observabilityfeature, snap, run, step_context
    ):
        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "test-model"
        step = observability_feature.IntegrateRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    @pytest.mark.parametrize("ready_checks", [1, 2])
    def test_run_waiting_timedout(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
        ready_checks,
    ):
        jhelper.wait_application_ready.side_effect = [None] * (ready_checks - 1) + [
            TimeoutError("timed out")
        ]

        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "test-model"
        step = observability_feature.IntegrateRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        assert jhelper.wait_application_ready.call_count == ready_checks
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_maas_includes_infra_model(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        """For MAAS deployments, infra_model is included in the integration loop."""
        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "openstack-machines"
        deployment.infra_model = "openstack-infra"

        with patch(
            "sunbeam.features.observability.feature.is_maas_deployment",
            return_value=True,
        ):
            step = observability_feature.IntegrateRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        # Both openstack collectors and the machines and infra collectors.
        assert jhelper.wait_application_ready.call_count == 4
        assert result.result_type == ResultType.COMPLETED

    def test_run_non_maas_excludes_infra_model(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        """For non-MAAS deployments, only openstack and machines models are used."""
        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "openstack-machines"

        with patch(
            "sunbeam.features.observability.feature.is_maas_deployment",
            return_value=False,
        ):
            step = observability_feature.IntegrateRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        # Both openstack collectors and the machines collector.
        assert jhelper.wait_application_ready.call_count == 3
        assert result.result_type == ResultType.COMPLETED


@pytest.mark.usefixtures("remote_cos_model_names")
class TestRemoveRemoteCosOffersStep:
    @pytest.mark.parametrize("maas", [False, True])
    @pytest.mark.parametrize("loki_relation", [False, True])
    def test_run_removes_relations_from_all_collectors(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        step_context,
        run,
        maas,
        loki_relation,
    ):
        deployment.openstack_machines_model = "openstack-machines"
        deployment.infra_model = "openstack-infra"
        models = {
            "openstack": ["opentelemetry-collector", "opentelemetry-collector-infra"],
            "openstack-machines": ["opentelemetry-collector"],
        }
        if maas:
            models["openstack-infra"] = ["opentelemetry-collector"]
        relations = {
            "grafana-dashboards-provider": [AppStatusRelation(related_app="grafana")],
            "send-remote-write": [
                AppStatusRelation(related_app="prometheus"),
                AppStatusRelation(related_app="prometheus-secondary"),
            ],
        }
        if loki_relation:
            relations["send-loki-logs"] = [AppStatusRelation(related_app="loki")]
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    app: AppStatus(
                        charm="opentelemetry-collector",
                        charm_origin="charmhub",
                        charm_name="opentelemetry-collector",
                        charm_rev=1,
                        exposed=False,
                        relations={
                            **relations,
                            "unrelated": [AppStatusRelation(related_app="other")],
                        },
                    )
                    for app in apps
                }
            )
            for apps in models.values()
        ]

        with (
            patch(
                "sunbeam.features.observability.feature.is_maas_deployment",
                return_value=maas,
            ),
            patch.object(
                observability_feature.RemoveRemoteCosOffersStep,
                "_get_juju_binary",
                return_value="juju",
            ),
        ):
            step = observability_feature.RemoveRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        assert jhelper.get_model_status.call_args_list == [
            mock_call(model) for model in models
        ]
        assert [call.args[0] for call in run.call_args_list] == [
            [
                "juju",
                "remove-relation",
                "-m",
                f"admin/{model}",
                f"{app}:{endpoint}",
                related.related_app,
            ]
            for model, apps in models.items()
            for app in apps
            for endpoint, related_apps in relations.items()
            for related in related_apps
        ]
        assert jhelper.wait_application_ready.call_args_list == [
            mock_call(
                app,
                model,
                accepted_status=["blocked"],
                timeout=observability_feature.OBSERVABILITY_DEPLOY_TIMEOUT,
            )
            for model, apps in models.items()
            for app in apps
        ]
        assert result.result_type == ResultType.COMPLETED

    def test_run(
        self, deployment, jhelper, observabilityfeature, snap, run, step_context
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "opentelemetry-collector": Mock(
                        relations={
                            "send-loki-logs": [AppStatusRelation(related_app="loki")]
                        }
                    )
                }
            ),
            Mock(
                apps={
                    "openstack-hypervisor": Mock(
                        relations={"identity-service": "keystone:identity_service"}
                    )
                }
            ),
        ]
        step = observability_feature.RemoveRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        run.assert_called_once()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_no_remote_offers(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [Mock(apps={}), Mock(apps={})]
        step = observability_feature.RemoveRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        run.assert_not_called()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    @pytest.mark.parametrize("ready_checks", [1, 2])
    def test_run_waiting_timedout(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
        ready_checks,
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "opentelemetry-collector": Mock(
                        relations={
                            "send-loki-logs": [AppStatusRelation(related_app="loki")]
                        }
                    )
                }
            ),
            Mock(
                apps={
                    "openstack-hypervisor": Mock(
                        relations={"identity-service": "keystone:identity_service"}
                    )
                }
            ),
        ]
        jhelper.wait_application_ready.side_effect = [None] * (ready_checks - 1) + [
            TimeoutError("timed out")
        ]
        step = observability_feature.RemoveRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        run.assert_called_once()
        assert jhelper.wait_application_ready.call_count == ready_checks
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_maas_includes_infra_model(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        """For MAAS deployments, infra_model is included in the remove loop."""
        deployment.openstack_machines_model = "openstack-machines"
        deployment.infra_model = "openstack-infra"
        # Return empty apps for all 3 models (openstack, machines, infra)
        jhelper.get_model_status.side_effect = [
            Mock(apps={}),
            Mock(apps={}),
            Mock(apps={}),
        ]
        with patch(
            "sunbeam.features.observability.feature.is_maas_deployment",
            return_value=True,
        ):
            step = observability_feature.RemoveRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        # Both OpenStack collectors and the machine and MAAS infra collectors.
        assert jhelper.wait_application_ready.call_count == 4
        assert result.result_type == ResultType.COMPLETED

    def test_run_non_maas_excludes_infra_model(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        """For non-MAAS deployments, only openstack and machines models are used."""
        deployment.openstack_machines_model = "openstack-machines"
        # Return empty apps for 2 models (openstack, machines)
        jhelper.get_model_status.side_effect = [Mock(apps={}), Mock(apps={})]
        with patch(
            "sunbeam.features.observability.feature.is_maas_deployment",
            return_value=False,
        ):
            step = observability_feature.RemoveRemoteCosOffersStep(
                deployment, observabilityfeature, jhelper
            )
            result = step.run(step_context)

        # Both OpenStack collectors and the machine collector.
        assert jhelper.wait_application_ready.call_count == 3
        assert result.result_type == ResultType.COMPLETED


class TestExternalObservabilityEnablePlans:
    """Test enablement plans for ExternalObservabilityFeature."""

    def _run_enable_plans(self, feature, deployment, run_plan_obs, is_maas):
        feature._manifest = Mock()
        with patch(
            "sunbeam.features.observability.feature.is_maas_deployment",
            return_value=is_maas,
        ):
            feature.run_enable_plans(deployment, Mock(), False)

        return [step for call in run_plan_obs.call_args_list for step in call.args[0]]

    def test_maas_infra_agent_step_accepts_blocked(
        self, deployment, run_plan_obs, juju_helper_obs
    ):
        """External COS: infra agent is blocked until offers are integrated.

        With an external COS, integrations are only created by
        IntegrateRemoteCosOffersStep after all agents are deployed, so the
        infra agent deploy step must accept 'blocked' status (LP#2159965).
        """
        feature = observability_feature.ExternalObservabilityFeature()
        steps = self._run_enable_plans(feature, deployment, run_plan_obs, is_maas=True)

        infra_steps = [
            step
            for step in steps
            if isinstance(step, observability_feature.DeployObservabilityAgentInfraStep)
        ]
        assert len(infra_steps) == 1
        assert "blocked" in infra_steps[0].accepted_app_status
        assert "active" in infra_steps[0].accepted_app_status

    def test_maas_integrate_offers_runs_after_infra_agent(
        self, deployment, run_plan_obs, juju_helper_obs
    ):
        """Offers are integrated only after the infra agent is deployed."""
        feature = observability_feature.ExternalObservabilityFeature()
        steps = self._run_enable_plans(feature, deployment, run_plan_obs, is_maas=True)

        step_types = [type(step) for step in steps]
        assert step_types.index(
            observability_feature.IntegrateRemoteCosOffersStep
        ) > step_types.index(observability_feature.DeployObservabilityAgentInfraStep)


class TestObservabilityFeatureTimeouts:
    """Test timeout calculation for ObservabilityFeature."""

    def test_set_application_timeout_on_enable_single_control(self, deployment):
        """Test timeout calculation with 1 control node."""
        deployment.get_client().cluster.list_nodes_by_role.return_value = ["node1"]
        feature = observability_feature.EmbeddedObservabilityFeature()

        timeout = feature.set_application_timeout_on_enable(deployment)

        deployment.get_client().cluster.list_nodes_by_role.assert_called_once_with(
            "control"
        )
        assert timeout == observability_feature.OBSERVABILITY_AGENT_K8S_DEPLOY_TIMEOUT

    def test_set_application_timeout_on_enable_multiple_control(self, deployment):
        """Test timeout calculation with multiple control nodes."""
        deployment.get_client().cluster.list_nodes_by_role.return_value = [
            "node1",
            "node2",
            "node3",
        ]
        feature = observability_feature.EmbeddedObservabilityFeature()

        timeout = feature.set_application_timeout_on_enable(deployment)

        deployment.get_client().cluster.list_nodes_by_role.assert_called_once_with(
            "control"
        )
        assert (
            timeout == observability_feature.OBSERVABILITY_AGENT_K8S_DEPLOY_TIMEOUT * 3
        )


class TestCosStorage:
    """Test COS charm storage helpers."""

    def test_storage_from_manifest(self):
        """Charms with storage in manifest are extracted."""
        manifest = Manifest(
            **{
                "features": {
                    "observability": {
                        "embedded": {
                            "software": {
                                "charms": {
                                    "prometheus-k8s": {"storage": {"database": "8G"}},
                                    "loki-k8s": {
                                        "storage": {
                                            "active-index-directory": "16G",
                                            "loki-chunks": "16G",
                                        }
                                    },
                                }
                            }
                        }
                    }
                }
            }
        )

        result = observability_feature.get_cos_storage_from_manifest(manifest)

        assert result == {
            "prometheus-storage": {"database": "8G"},
            "loki-storage": {"active-index-directory": "16G", "loki-chunks": "16G"},
        }

    def test_storage_from_manifest_empty(self):
        """No charms with storage returns empty dict."""
        manifest = Manifest()
        assert not observability_feature.get_cos_storage_from_manifest(manifest)

    def test_storage_from_manifest_non_dict(self):
        """Non-dict storage values are ignored."""
        manifest = MagicMock()
        charm = MagicMock()
        charm.model_extra = {"storage": "not-a-dict"}
        manifest.find_charm.return_value = charm
        assert not observability_feature.get_cos_storage_from_manifest(manifest)

    def test_storage_dict_empty(self, read_config_obs):
        """Returns empty when DB and manifest have no storage."""
        read_config_obs.side_effect = ConfigItemNotFoundException("not found")
        manifest = Manifest()
        assert not observability_feature.get_cos_storage_dict(Mock(), manifest)

    def test_storage_dict_from_db(self, read_config_obs):
        """DB values are returned."""
        read_config_obs.return_value = {"prometheus-storage": {"database": "8G"}}
        manifest = Manifest()
        result = observability_feature.get_cos_storage_dict(Mock(), manifest)
        assert result == {"prometheus-storage": {"database": "8G"}}

    def test_storage_dict_manifest_overrides_db(self, read_config_obs):
        """Manifest values override DB values."""
        read_config_obs.return_value = {"prometheus-storage": {"database": "4G"}}
        manifest = Manifest(
            **{
                "features": {
                    "observability": {
                        "embedded": {
                            "software": {
                                "charms": {
                                    "prometheus-k8s": {"storage": {"database": "16G"}}
                                }
                            }
                        }
                    }
                }
            }
        )
        result = observability_feature.get_cos_storage_dict(Mock(), manifest)
        assert result["prometheus-storage"] == {"database": "16G"}

    def test_storage_dict_deep_merges(self, read_config_obs):
        """Partial manifest merges with DB, not replaces."""
        read_config_obs.return_value = {
            "loki-storage": {"active-index-directory": "4G", "loki-chunks": "4G"}
        }
        manifest = Manifest(
            **{
                "features": {
                    "observability": {
                        "embedded": {
                            "software": {
                                "charms": {
                                    "loki-k8s": {"storage": {"loki-chunks": "8G"}}
                                }
                            }
                        }
                    }
                }
            }
        )
        result = observability_feature.get_cos_storage_dict(Mock(), manifest)
        assert result["loki-storage"] == {
            "active-index-directory": "4G",
            "loki-chunks": "8G",
        }


class TestObservabilityFeaturePostEnable:
    """Test the post_enable grant access logic."""

    def test_manifest_tfvar_map_supports_collector_storage(self):
        """OpenStack plan maps collector storage manifest fields to tfvars."""
        feature = observability_feature.EmbeddedObservabilityFeature()

        collector_map = feature.manifest_attributes_tfvar_map()[feature.tfplan][
            "charms"
        ]["opentelemetry-collector-k8s"]

        assert collector_map["storage"] == "opentelemetry-collector-storage"
        assert collector_map["storage-map"] == "opentelemetry-collector-storage-map"

    def test_post_enable_grants_access_to_all_nodes(
        self, deployment, update_config, run_plan_obs, juju_helper_obs
    ):
        """All nodes get JujuGrantModelAccessStep called via run_plan."""
        deployment.get_client.return_value.cluster.list_juju_users.return_value = [
            {"username": "node-1", "token": "t"},
            {"username": "node-2", "token": "t"},
            {"username": "node-3", "token": "t"},
        ]
        feature = observability_feature.EmbeddedObservabilityFeature()

        feature.post_enable(deployment, MagicMock(), show_hints=False)

        assert run_plan_obs.call_count == 3
        for i, call in enumerate(run_plan_obs.call_args_list, start=1):
            plan = call[0][0]
            assert len(plan) == 1
            step = plan[0]
            assert isinstance(step, observability_feature.JujuGrantModelAccessStep)
            assert step.username == f"node-{i}"
            assert step.model == observability_feature.OBSERVABILITY_MODEL

    def test_post_enable_handles_grant_failure_gracefully(
        self, deployment, update_config, run_plan_obs, juju_helper_obs
    ):
        """If granting access fails for one node, others are still processed."""
        deployment.get_client.return_value.cluster.list_juju_users.return_value = [
            {"username": "node-1", "token": "t"},
            {"username": "node-2", "token": "t"},
            {"username": "node-3", "token": "t"},
        ]
        run_plan_obs.side_effect = [None, Exception("grant failed"), None]
        feature = observability_feature.EmbeddedObservabilityFeature()

        feature.post_enable(deployment, MagicMock(), show_hints=False)

        assert run_plan_obs.call_count == 3


class TestDeployHardwareObserverStep:
    def test_run(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Happy path: terraform applies against sunbeam-machine, wait succeeds."""
        step = observability_feature.DeployHardwareObserverStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        override = tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override["principal-applications"] == ["sunbeam-machine"]
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Terraform failure returns FAILED without waiting."""
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployHardwareObserverStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Timeout waiting for hardware-observer returns FAILED."""
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = observability_feature.DeployHardwareObserverStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_accepted_status_includes_blocked(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Default accepted_app_status allows blocked so the step does not fail."""
        step = observability_feature.DeployHardwareObserverStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        assert "blocked" in step.accepted_app_status
        assert "active" in step.accepted_app_status


class TestRemoveHardwareObserverStep:
    def test_run(
        self,
        deployment,
        tfhelper,
        jhelper,
        observabilityfeature,
        update_config,
        step_context,
    ):
        """Happy path: destroy succeeds, app gone, config cleared."""
        step = observability_feature.RemoveHardwareObserverStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        waited_apps = jhelper.wait_application_gone.call_args.args[0]
        assert waited_apps == ["hardware-observer"]
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Terraform destroy failure returns FAILED without waiting."""
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveHardwareObserverStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature, step_context
    ):
        """Timeout waiting for app to be gone returns FAILED."""
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveHardwareObserverStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run(step_context)

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestTerraformChannelDefaults:
    """Terraform plan channel defaults should stay in sync with feature.py constants."""

    def _tfvar_dir(self) -> Path:
        return Path(observability_feature.__file__).parent / "etc"

    def _channel_default(self, tf_file: Path, variable: str) -> str:
        text = tf_file.read_text()
        pattern = (
            r'variable\s+"' + re.escape(variable) + r'"\s*\{'
            r'[^}]*?default\s*=\s*"([^"]+)"'
        )
        match = re.search(pattern, text, re.DOTALL)
        assert match, f"no default found for {variable} in {tf_file.name}"
        return match.group(1)

    def test_cos_channel_defaults_match_cos_channel(self):
        tf_file = self._tfvar_dir() / "deploy-cos" / "variables.tf"
        for variable in (
            "cos-channel",
            "alertmanager-channel",
            "prometheus-channel",
            "grafana-channel",
            "catalogue-channel",
            "loki-channel",
        ):
            assert (
                self._channel_default(tf_file, variable)
                == observability_feature.COS_CHANNEL
            )

    def test_traefik_channel_default_matches_traefik_channel(self):
        tf_file = self._tfvar_dir() / "deploy-cos" / "variables.tf"
        assert (
            self._channel_default(tf_file, "traefik-channel")
            == observability_feature.TRAEFIK_CHANNEL
        )

    def test_collector_channel_default_matches_collector_channel(self):
        tf_file = self._tfvar_dir() / "deploy-grafana-agent" / "variables.tf"
        assert (
            self._channel_default(tf_file, "opentelemetry-collector-channel")
            == observability_feature.OPENTELEMETRY_COLLECTOR_CHANNEL
        )

    def test_hardware_observer_channel_default_matches_hardware_observer_channel(self):
        tf_file = self._tfvar_dir() / "deploy-hardware-observer" / "variables.tf"
        assert (
            self._channel_default(tf_file, "hardware-observer-channel")
            == observability_feature.HARDWARE_OBSERVER_CHANNEL
        )


@pytest.fixture
def readiness_states(deployment):
    from sunbeam.feature_manager import FeatureManager
    from sunbeam.features.loadbalancer.feature import LoadbalancerFeature
    from sunbeam.features.vault.feature import VaultFeature

    manager = object.__new__(FeatureManager)
    manager._features = {
        "vault": VaultFeature(),
        "loadbalancer": LoadbalancerFeature(),
    }
    for feature in manager._features.values():
        feature.is_enabled = Mock(return_value=True)
    deployment.get_feature_manager.return_value = manager
    deployment.get_client.return_value.cluster.get_config.return_value = (
        '{"database": "single"}'
    )

    def state(apps, relations=()):
        resources = [
            {
                "mode": "managed",
                "type": "juju_application",
                "instances": [
                    {"attributes": {"name": app, "units": 1, "machines": None}}
                ],
            }
            for app in apps
        ]
        resources.extend(
            {
                "mode": "managed",
                "type": "juju_integration",
                "instances": [
                    {
                        "attributes": {
                            "application": [
                                {
                                    "name": left,
                                    "endpoint": left_endpoint,
                                    "offer_url": None,
                                },
                                {
                                    "name": right,
                                    "endpoint": right_endpoint,
                                    "offer_url": None,
                                },
                            ]
                        }
                    }
                ],
            }
            for left, left_endpoint, right, right_endpoint in relations
        )
        return {"resources": resources}

    deployment.openstack_machines_model = "machines"
    deployment.infra_model = "infra"
    deployment.get_client.return_value.cluster.list_nodes_by_role.return_value = [
        {"machineid": machine} for machine in range(3)
    ]
    helpers = {
        "openstack-plan": Mock(
            pull_state=Mock(
                return_value=state(
                    [
                        "opentelemetry-collector",
                        "opentelemetry-collector-infra",
                        "ceilometer",
                    ],
                    [
                        (
                            "ceilometer",
                            "logging",
                            "opentelemetry-collector",
                            "receive-loki-logs",
                        )
                    ],
                )
            )
        ),
        "grafana-agent-plan": Mock(
            pull_state=Mock(
                return_value=state(
                    ["opentelemetry-collector"],
                    [
                        ("opentelemetry-collector", "cos-agent", app, "cos-agent")
                        for app in ("k8s", "microceph", "openstack-hypervisor")
                    ]
                    + [
                        (
                            "opentelemetry-collector",
                            "juju-info",
                            "sunbeam-machine",
                            "juju-info",
                        )
                    ],
                )
            )
        ),
        "hardware-observer-plan": Mock(
            pull_state=Mock(
                return_value=state(
                    ["hardware-observer"],
                    [
                        (
                            "hardware-observer",
                            "general-info",
                            "sunbeam-machine",
                            "juju-info",
                        ),
                        (
                            "hardware-observer",
                            "cos-agent",
                            "opentelemetry-collector",
                            "cos-agent",
                        ),
                    ],
                )
            )
        ),
        "cos-plan": Mock(
            pull_state=Mock(return_value=state(["prometheus", "loki", "traefik"]))
        ),
        "observability-agent-infra-plan": Mock(
            pull_state=Mock(
                return_value=state(
                    ["opentelemetry-collector"],
                    [
                        (
                            "opentelemetry-collector",
                            "juju-info",
                            "sunbeam-clusterd",
                            "juju-info",
                        )
                    ],
                )
            )
        ),
    }
    deployment.get_tfhelper.side_effect = helpers.__getitem__
    return helpers


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("maas", [False, True])
def test_final_readiness_scope_follows_intended_integrations(
    deployment,
    jhelper,
    readiness_states,
    mocker,
    external,
    maas,
):
    feature_type = (
        observability_feature.ExternalObservabilityFeature
        if external
        else observability_feature.EmbeddedObservabilityFeature
    )
    feature = feature_type()
    feature.grafana_offer_url = "other:owner/cos.grafana-dashboards"
    feature.prometheus_offer_url = "other:owner/cos.prometheus-write"
    feature.loki_offer_url = "other:owner/cos.loki-logging"
    jhelper.get_machines.return_value = {"0": Mock(), "1": Mock(), "2": Mock()}
    mocker.patch(
        "sunbeam.features.observability.feature.is_maas_deployment", return_value=maas
    )
    requirements = feature._readiness_requirements(deployment, jhelper)
    assert set(requirements) == (
        {"openstack", "machines"}
        | ({"observability"} if not external else set())
        | ({"infra"} if maas else set())
    )
    assert set(requirements["openstack"]) == {
        "opentelemetry-collector",
        "opentelemetry-collector-infra",
        "ceilometer",
    }
    assert requirements["openstack"]["ceilometer"].relations == {
        "logging": {"opentelemetry-collector"}
    }
    machine_requirements = requirements["machines"]
    collector = machine_requirements["opentelemetry-collector"]
    assert collector.units is None
    assert collector.relations["cos-agent"] == {
        "k8s",
        "microceph",
        "openstack-hypervisor",
        "hardware-observer",
    }
    assert collector.principals == {
        app: {"0", "1", "2"}
        for app in ("sunbeam-machine", "k8s", "microceph", "openstack-hypervisor")
    }
    hardware = machine_requirements["hardware-observer"]
    assert hardware.principals == {"sunbeam-machine": {"0", "1", "2"}}
    assert hardware.status == ["active", "blocked"]
    assert hardware.agent_status == ("idle",)
    if maas:
        assert requirements["infra"]["opentelemetry-collector"].principals == {
            "sunbeam-clusterd": ["0", "1", "2"]
        }
    if external:
        assert collector.relations["send-loki-logs"] == {"loki-logging"}
        assert requirements["openstack"]["opentelemetry-collector-infra"].relations[
            "send-loki-logs"
        ] == {"loki-logging"}
    jhelper.get_model_status.assert_not_called()


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize(
    "app,workload,message,ready",
    [
        ("vault", "active", "", True),
        (
            "vault",
            "blocked",
            "Please initialize Vault or integrate with an auto-unseal provider",
            True,
        ),
        ("vault", "blocked", "Please unseal Vault", True),
        (
            "vault",
            "blocked",
            "Please authorize charm (see `authorize-charm` action)",
            True,
        ),
        ("vault", "blocked", "Vault failed to start", False),
        ("vault", "waiting", "Please unseal Vault", False),
        ("octavia", "active", "Unit is ready", True),
        (
            "octavia",
            "waiting",
            loadbalancer_feature.OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE,
            True,
        ),
        (
            "octavia",
            "blocked",
            loadbalancer_feature.OCTAVIA_AMPHORA_RELATIONS_MISSING_MESSAGE,
            True,
        ),
        (
            "octavia",
            "blocked",
            loadbalancer_feature.OCTAVIA_AMPHORA_CA_CERT_MESSAGE,
            True,
        ),
        (
            "octavia",
            "blocked",
            loadbalancer_feature.OCTAVIA_AMPHORA_CONTROLLER_CERT_MESSAGE,
            True,
        ),
        ("octavia", "blocked", "Database relation not ready", False),
        ("octavia", "waiting", "Database relation not ready", False),
    ],
)
def test_observability_preserves_supported_provider_waits(
    deployment,
    readiness_states,
    jhelper,
    mocker,
    external,
    app,
    workload,
    message,
    ready,
):
    resources = readiness_states["openstack-plan"].pull_state.return_value["resources"]
    resources.extend(
        [
            {
                "mode": "managed",
                "type": "juju_application",
                "instances": [{"attributes": {"name": app, "units": 3}}],
            },
            {
                "mode": "managed",
                "type": "juju_integration",
                "instances": [
                    {
                        "attributes": {
                            "application": [
                                {"name": app, "endpoint": "logging"},
                                {
                                    "name": "opentelemetry-collector-infra",
                                    "endpoint": "receive-loki-logs",
                                },
                            ]
                        }
                    }
                ],
            },
        ]
    )
    mocker.patch(
        "sunbeam.features.observability.feature.is_maas_deployment", return_value=False
    )
    feature_type = (
        observability_feature.ExternalObservabilityFeature
        if external
        else observability_feature.EmbeddedObservabilityFeature
    )
    requirements = feature_type()._readiness_requirements(deployment, jhelper)
    requirement = requirements["openstack"][app]
    status = Mock()
    application = Mock()
    status.apps = {app: application}
    application.app_status.current = workload
    application.app_status.message = message
    application.relations = {
        "logging": [Mock(related_app="opentelemetry-collector-infra")]
    }
    units = {f"{app}/{n}": Mock() for n in range(3)}
    for unit in units.values():
        unit.workload_status.current = workload
        unit.workload_status.message = message
        unit.juju_status.current = "idle"
    status.get_units.return_value = units
    assert (not requirement.pending(status, app)) is ready
    assert requirement.agent_status == ("idle",)
    assert requirement.units == 3
    assert requirement.relations == {"logging": {"opentelemetry-collector-infra"}}
    for collector in ("opentelemetry-collector", "opentelemetry-collector-infra"):
        assert requirements["openstack"][collector].status == ("active",)
        assert requirements["openstack"][collector].agent_status == ("idle",)
    if ready:
        units[f"{app}/2"].juju_status.current = "executing"
        assert any(
            f"{app}/2: agent 'executing'" in reason
            for reason in requirement.pending(status, app)
        )


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("region_controller", [False, True])
def test_machine_readiness_uses_union_of_node_roles(
    deployment, readiness_states, external, region_controller
):
    feature_type = (
        observability_feature.ExternalObservabilityFeature
        if external
        else observability_feature.EmbeddedObservabilityFeature
    )
    nodes = [
        {
            "machineid": machine,
            "role": ["control", "compute", "storage"]
            if machine < 3
            else ["compute", "storage"],
        }
        for machine in range(5)
    ]
    nodes.append({"machineid": -1, "role": ["control"]})
    if region_controller:
        nodes.append({"machineid": 5, "role": ["region_controller"]})

    def nodes_matching_roles(role):
        requested = {role} if isinstance(role, str) else set(role)
        return [node for node in nodes if requested.issubset(node["role"])]

    deployment.get_client.return_value.cluster.list_nodes_by_role.side_effect = (
        nodes_matching_roles
    )
    requirements = feature_type()._machine_readiness_requirements(deployment)
    workload_machines = {str(machine) for machine in range(5)}
    control_machines = {"0", "1", "2"}
    if region_controller:
        workload_machines.add("5")
        control_machines.add("5")
    assert requirements["sunbeam-machine"].machines == workload_machines
    assert requirements["k8s"].machines == control_machines
    assert requirements["opentelemetry-collector"].principals == {
        "sunbeam-machine": workload_machines,
        "k8s": control_machines,
        "microceph": {"0", "1", "2", "3", "4"},
        "openstack-hypervisor": {"0", "1", "2", "3", "4"},
    }
    assert requirements["hardware-observer"].principals == {
        "sunbeam-machine": workload_machines
    }


@pytest.mark.parametrize("external", [False, True])
def test_observability_initializes_microovn_before_readiness_state_pull(
    deployment, readiness_states, jhelper, mocker, external
):
    from click.testing import CliRunner

    from sunbeam.core.common import run_plan
    from sunbeam.core.manifest import FeatureConfig

    initialized = False
    microovn = Mock()

    def initialize():
        nonlocal initialized
        initialized = True

    def pull_state():
        if not initialized:
            raise TerraformException("Backend initialization required")
        return {
            "resources": [
                {
                    "mode": "managed",
                    "type": "juju_application",
                    "instances": [
                        {
                            "attributes": {
                                "name": "microovn",
                                "units": 0,
                                "machines": ["0", "1", "2"],
                            }
                        }
                    ],
                }
            ]
        }

    microovn.init.side_effect = initialize
    microovn.pull_state.side_effect = pull_state
    readiness_states["microovn-plan"] = microovn
    agent_state = readiness_states["grafana-agent-plan"].pull_state.return_value
    agent_state["resources"].append(
        {
            "mode": "managed",
            "type": "juju_integration",
            "instances": [
                {
                    "attributes": {
                        "application": [
                            {
                                "name": "opentelemetry-collector",
                                "endpoint": "cos-agent",
                            },
                            {"name": "microovn", "endpoint": "cos-agent"},
                        ]
                    }
                }
            ],
        }
    )
    mocker.patch(
        "sunbeam.features.observability.feature.JujuHelper", return_value=jhelper
    )
    mocker.patch(
        "sunbeam.features.observability.feature.is_maas_deployment", return_value=False
    )
    feature_type = (
        observability_feature.ExternalObservabilityFeature
        if external
        else observability_feature.EmbeddedObservabilityFeature
    )
    feature = feature_type()
    feature._manifest = Mock()

    def run_initialization_and_readiness(plan, console, show_hints):
        selected = [
            step
            for step in plan
            if isinstance(
                step,
                (
                    observability_feature.TerraformInitStep,
                    observability_feature.WaitForFeatureReadyStep,
                ),
            )
        ]
        return run_plan(selected, console, show_hints) if selected else {}

    mocker.patch(
        "sunbeam.features.observability.feature.run_plan",
        side_effect=run_initialization_and_readiness,
    )

    @click.command()
    def enable():
        feature.run_enable_plans(deployment, FeatureConfig(), False)

    result = CliRunner().invoke(enable)
    assert result.exit_code == 0, result.output
    assert "Observability enabled" in result.output
    assert [call[0] for call in microovn.mock_calls] == ["init", "pull_state"]
    requirements = jhelper.wait_until_models_ready.call_args.args[0]
    assert requirements["machines"]["microovn"].machines == ["0", "1", "2"]


@pytest.mark.parametrize("external", [False, True])
def test_observability_final_gate_failure_reaches_cli(deployment, mocker, external):
    from click.testing import CliRunner

    from sunbeam.core.common import run_plan
    from sunbeam.core.manifest import FeatureConfig

    feature_type = (
        observability_feature.ExternalObservabilityFeature
        if external
        else observability_feature.EmbeddedObservabilityFeature
    )
    feature = feature_type()
    feature._manifest = Mock()
    mocker.patch.object(feature, "pre_enable")
    post_enable = mocker.patch.object(feature, "post_enable")
    update_feature_info = mocker.patch.object(feature, "update_feature_info")
    mocker.patch.object(feature, "_readiness_requirements", return_value={})
    helper = mocker.patch(
        "sunbeam.features.observability.feature.JujuHelper"
    ).return_value
    helper.wait_until_models_ready.side_effect = TimeoutError(
        "hardware-observer/2: agent executing"
    )
    mocker.patch(
        "sunbeam.features.observability.feature.is_maas_deployment", return_value=False
    )

    def run_final_only(plan, console, show_hints):
        final = [
            step
            for step in plan
            if isinstance(step, observability_feature.WaitForFeatureReadyStep)
        ]
        if final:
            return run_plan(final, console, show_hints)
        return {}

    mocker.patch(
        "sunbeam.features.observability.feature.run_plan", side_effect=run_final_only
    )

    @click.command()
    def enable():
        feature.enable_feature(deployment, FeatureConfig(), False)

    result = CliRunner().invoke(enable)
    assert result.exit_code == 1
    assert "hardware-observer/2: agent executing" in result.output
    assert "Observability enabled" not in result.output
    post_enable.assert_not_called()
    update_feature_info.assert_not_called()
    helper.wait_until_models_ready.assert_called_once()
    assert helper.wait_until_models_ready.call_args.args[1] == 7200
