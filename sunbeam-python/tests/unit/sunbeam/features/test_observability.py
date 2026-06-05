# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock, Mock, patch

import click
import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformException
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


class TestIntegrateRemoteCosOffersStep:
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

    def test_run_waiting_timedout(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "test-model"
        step = observability_feature.IntegrateRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveRemoteCosOffersStep:
    def test_run(
        self, deployment, jhelper, observabilityfeature, snap, run, step_context
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "opentelemetry-collector": Mock(
                        relations={"logging-consumer": "loki:loki_push_api"}
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

    def test_run_waiting_timedout(
        self,
        deployment,
        jhelper,
        observabilityfeature,
        snap,
        run,
        step_context,
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "opentelemetry-collector": Mock(
                        relations={"logging-consumer": "loki:loki_push_api"}
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
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")
        step = observability_feature.RemoveRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run(step_context)
        run.assert_called_once()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


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

    def test_post_enable_grants_access_to_all_nodes(
        self, deployment, update_config, run_plan_obs, juju_helper_obs
    ):
        """All nodes get JujuGrantModelAccessStep called via run_plan."""
        deployment.get_client.return_value.cluster.list_nodes.return_value = [
            {"name": "node-1"},
            {"name": "node-2"},
            {"name": "node-3"},
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
        deployment.get_client.return_value.cluster.list_nodes.return_value = [
            {"name": "node-1"},
            {"name": "node-2"},
            {"name": "node-3"},
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
