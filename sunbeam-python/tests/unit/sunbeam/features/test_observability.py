# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.terraform import TerraformException
from sunbeam.features.observability import feature as observability_feature


@pytest.fixture()
def tfhelper():
    yield Mock()


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def deployment():
    yield Mock()


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


class TestDeployObservabilityStackStep:
    def test_run(self, deployment, tfhelper, jhelper, observabilityfeature, ssnap):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        jhelper.get_application_names.return_value = [1, 2, 3]
        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap
    ):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap
    ):
        ssnap().config.get.return_value = "k8s"
        observabilityfeature.deployment.proxy_settings.return_value = {}
        jhelper.get_application_names.return_value = [1, 2, 3]
        jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        step = observability_feature.DeployObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveObservabilityStackStep:
    def test_run(self, deployment, tfhelper, jhelper, observabilityfeature, ssnap):
        ssnap().config.get.return_value = "k8s"
        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap
    ):
        ssnap().config.get.return_value = "k8s"
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature, ssnap
    ):
        ssnap().config.get.return_value = "k8s"
        jhelper.wait_model_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveObservabilityStackStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_model_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestDeployGrafanaAgentStep:
    def test_run(self, deployment, tfhelper, jhelper, observabilityfeature):
        step = observability_feature.DeployGrafanaAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature
    ):
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = observability_feature.DeployGrafanaAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature
    ):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = observability_feature.DeployGrafanaAgentStep(
            deployment, Mock(), observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveGrafanaAgentStep:
    def test_run(
        self, deployment, tfhelper, jhelper, observabilityfeature, update_config
    ):
        step = observability_feature.RemoveGrafanaAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_destroy_failed(
        self, deployment, tfhelper, jhelper, observabilityfeature
    ):
        tfhelper.destroy.side_effect = TerraformException("destroy failed...")

        step = observability_feature.RemoveGrafanaAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "destroy failed..."

    def test_run_waiting_timed_out(
        self, deployment, tfhelper, jhelper, observabilityfeature
    ):
        jhelper.wait_application_gone.side_effect = TimeoutError("timed out")

        step = observability_feature.RemoveGrafanaAgentStep(
            deployment, observabilityfeature, tfhelper, jhelper
        )
        result = step.run()

        tfhelper.destroy.assert_called_once()
        jhelper.wait_application_gone.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestIntegrateRemoteCosOffersStep:
    def test_run(self, deployment, jhelper, observabilityfeature, snap, run):
        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "test-model"
        step = observability_feature.IntegrateRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_waiting_timedout(
        self, deployment, jhelper, observabilityfeature, snap, run
    ):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        observabilityfeature.grafana_offer_url = "remotecos:admin/grafana"
        observabilityfeature.prometheus_offer_url = "remotecos:admin/prometheus"
        observabilityfeature.loki_offer_url = "remotecos:admin/loki"
        deployment.openstack_machines_model = "test-model"
        step = observability_feature.IntegrateRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveRemoteCosOffersStep:
    def test_run(self, deployment, jhelper, observabilityfeature, snap, run):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "grafana-agent": Mock(
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

        result = step.run()
        run.assert_called_once()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_no_remote_offers(
        self, deployment, jhelper, observabilityfeature, snap, run
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [Mock(apps={}), Mock(apps={})]
        step = observability_feature.RemoveRemoteCosOffersStep(
            deployment, observabilityfeature, jhelper
        )

        result = step.run()
        run.assert_not_called()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_waiting_timedout(
        self, deployment, jhelper, observabilityfeature, snap, run
    ):
        observabilityfeature.deployment.openstack_machines_model = "test-model"
        jhelper.get_model_status.side_effect = [
            Mock(
                apps={
                    "grafana-agent": Mock(
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

        result = step.run()
        run.assert_called_once()
        jhelper.wait_application_ready.assert_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
