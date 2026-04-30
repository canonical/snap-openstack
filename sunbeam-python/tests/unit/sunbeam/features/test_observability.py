# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

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

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


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
