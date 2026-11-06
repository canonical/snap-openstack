# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import httpx
import pytest
import tenacity
from lightkube.core.exceptions import ApiError
from lightkube.models import core_v1, meta_v1
from lightkube.resources.core_v1 import Service

from sunbeam.core.common import ResultType, Role
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.k8s import K8SHelper
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    PatchLoadBalancerServicesIPPoolStep,
    PatchLoadBalancerServicesIPStep,
    RemoveMachineUnitsStep,
    _get_lb_service,
)
from sunbeam.core.terraform import TerraformException, TerraformStateLockedException


@pytest.fixture()
def read_config():
    with patch("sunbeam.core.steps.read_config", return_value={}) as p:
        yield p


class TestDeployMachineApplicationStep:
    def test_run_pristine_installation(
        self,
        deployment,
        cclient,
        tfhelper,
        jhelper,
        manifest,
        step_context,
    ):
        jhelper.get_application.side_effect = ApplicationNotFoundException("not found")

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_deployed(
        self,
        deployment,
        cclient,
        tfhelper,
        jhelper,
        manifest,
        step_context,
    ):
        jhelper.get_model_owner.return_value = "admin"
        jhelper.get_model_uuid.return_value = "model-uuid"

        tfconfig = "tfconfig"
        machines = ["1", "2"]
        model = "model1"
        cclient.cluster.list_nodes_by_role.return_value = [
            {"name": "machine1", "machineid": "1"},
            {"name": "machine1", "machineid": "2"},
        ]

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            tfconfig,
            "app1",
            model,
            [Role.CONTROL],
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            cclient,
            manifest,
            tfvar_config=tfconfig,
            override_tfvars={
                "machine_ids": machines,
                "machine_model_uuid": "model-uuid",
            },
            tf_apply_extra_args=[],
            reporter=step_context.reporter,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self,
        deployment,
        cclient,
        tfhelper,
        jhelper,
        manifest,
        step_context,
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        jhelper.get_model_uuid.return_value = "model-uuid"
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_tf_apply_locked(
        self,
        deployment,
        cclient,
        tfhelper,
        jhelper,
        manifest,
        step_context,
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        tfhelper.update_tfvars_and_apply_tf.side_effect = [
            TerraformStateLockedException("apply failed..."),
            None,
        ]

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        step.run.retry.wait = tenacity.wait_none()
        result = step.run(step_context)

        tfhelper.update_tfvars_and_apply_tf.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_waiting_timed_out(
        self,
        deployment,
        cclient,
        tfhelper,
        jhelper,
        manifest,
        step_context,
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run(step_context)

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveMachineUnitStep:
    def test_is_skip(self, cclient, jhelper, step_context):
        id = "1"
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": id}]
        jhelper.get_application.return_value = Mock(units={"app1/0": Mock(machine=id)})

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip(step_context)

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper, step_context):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]

        jhelper.get_application.return_value = Mock(units={"app1/0": Mock()})
        step = RemoveMachineUnitsStep(
            cclient, "node-1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip(step_context)

        cclient.cluster.list_nodes.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self, cclient, jhelper, step_context):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip(step_context)

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self, cclient, jhelper, step_context):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]
        jhelper.get_application.return_value = Mock(units={})

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip(step_context)

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, jhelper, read_config, step_context):
        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(
        self, cclient, jhelper, read_config, step_context
    ):
        jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        step.units_to_remove = {"app1/0"}
        result = step.run(step_context)

        jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self, cclient, jhelper, read_config, step_context):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run(step_context)

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


def _api_error(code: int) -> ApiError:
    return ApiError(
        Mock(),
        httpx.Response(
            status_code=code,
            content=json.dumps({"code": code, "reason": "test"}),
        ),
    )


def _lb_service(name: str, ip: str | None = None) -> Service:
    return Service(
        metadata=meta_v1.ObjectMeta(name=name, annotations={}),
        spec=core_v1.ServiceSpec(type="LoadBalancer"),
        status=core_v1.ServiceStatus(
            loadBalancer=core_v1.LoadBalancerStatus(
                ingress=[core_v1.LoadBalancerIngress(ip=ip)] if ip else None
            )
        ),
    )


def _requested_names(kube: Mock) -> list[str]:
    return [c.args[1] for c in kube.get.call_args_list]


class TestGetLbService:
    def test_prefers_lb_service(self):
        kube = Mock()
        kube.get.return_value = _lb_service("bind-lb")

        assert _get_lb_service(kube, "bind").metadata.name == "bind-lb"
        assert _requested_names(kube) == ["bind-lb"]

    def test_falls_back_to_base_service(self):
        kube = Mock()
        kube.get.side_effect = [_api_error(404), _lb_service("bind")]

        assert _get_lb_service(kube, "bind").metadata.name == "bind"
        assert _requested_names(kube) == ["bind-lb", "bind"]

    def test_find_lb_false_looks_up_name_only(self):
        kube = Mock()
        kube.get.side_effect = _api_error(404)

        with pytest.raises(ApiError):
            _get_lb_service(kube, "bind", find_lb=False)
        assert _requested_names(kube) == ["bind"]

    def test_missing_service_raises_404(self):
        kube = Mock()
        kube.get.side_effect = _api_error(404)

        with pytest.raises(ApiError) as exc_info:
            _get_lb_service(kube, "consul-storage")
        assert exc_info.value.status.code == 404
        assert _requested_names(kube) == ["consul-storage-lb", "consul-storage"]

    @pytest.mark.parametrize(
        "find_lb, expected_lookups",
        [
            (True, ["consul-storage-lb-lb", "consul-storage-lb"]),
            (False, ["consul-storage-lb"]),
        ],
    )
    def test_missing_name_ending_in_lb_does_not_recurse(
        self, find_lb, expected_lookups
    ):
        """LP#2168498: a missing '<x>-lb' service used to recurse forever."""
        kube = Mock()
        kube.get.side_effect = _api_error(404)

        with pytest.raises(ApiError) as exc_info:
            _get_lb_service(kube, "consul-storage-lb", find_lb=find_lb)
        assert exc_info.value.status.code == 404
        assert _requested_names(kube) == expected_lookups

    def test_non_404_error_raises_immediately(self):
        kube = Mock()
        kube.get.side_effect = _api_error(500)

        with pytest.raises(ApiError) as exc_info:
            _get_lb_service(kube, "bind")
        assert exc_info.value.status.code == 500
        assert _requested_names(kube) == ["bind-lb"]


class _IPStep(PatchLoadBalancerServicesIPStep):
    def services(self) -> list[str]:
        return ["consul-storage"]

    def model(self) -> str:
        return "openstack"


class _IPPoolStep(PatchLoadBalancerServicesIPPoolStep):
    def services(self) -> list[str]:
        return ["consul-storage"]

    def model(self) -> str:
        return "openstack"


@pytest.fixture()
def missing_kube():
    kube = Mock()
    kube.get.side_effect = _api_error(404)
    return kube


class TestPatchLoadBalancerServicesMissingService:
    @pytest.mark.parametrize("step_cls", [_IPStep, _IPPoolStep])
    def test_run_ignore_errors_skips_missing(
        self, step_cls, missing_kube, step_context
    ):
        step = step_cls(Mock(), "storage-pool", ignore_errors=True)
        step.kube = missing_kube

        # Nothing was patched: report SKIPPED, like is_skip() does.
        assert step.run(step_context).result_type == ResultType.SKIPPED
        missing_kube.apply.assert_not_called()
        missing_kube.patch.assert_not_called()

    @pytest.mark.parametrize("step_cls", [_IPStep, _IPPoolStep])
    def test_run_ignore_errors_completes_if_any_service_found(
        self, step_cls, step_context
    ):
        """Only an all-skipped run is SKIPPED; one found service completes."""
        present = _lb_service("traefik-lb", ip="10.0.0.1")
        present.metadata.annotations = {
            K8SHelper.get_loadbalancer_ip_annotation(): "10.0.0.1",
            K8SHelper.get_loadbalancer_address_pool_annotation(): "storage-pool",
            K8SHelper.get_loadbalancer_allocated_pool_annotation(): "storage-pool",
        }

        def _get(_resource, name):
            if name == "traefik-lb":
                return present
            raise _api_error(404)

        kube = Mock()
        kube.get.side_effect = _get
        step = step_cls(Mock(), "storage-pool", ignore_errors=True)
        step.services = lambda: ["consul-storage", "traefik"]
        step.kube = kube

        assert step.run(step_context).result_type == ResultType.COMPLETED

    @pytest.mark.parametrize("step_cls", [_IPStep, _IPPoolStep])
    def test_run_without_ignore_errors_fails(
        self, step_cls, missing_kube, step_context
    ):
        step = step_cls(Mock(), "storage-pool", ignore_errors=False)
        step.kube = missing_kube

        assert step.run(step_context).result_type == ResultType.FAILED
        missing_kube.apply.assert_not_called()
        missing_kube.patch.assert_not_called()

    @patch("sunbeam.core.steps.l_client.Client")
    @patch("sunbeam.core.steps.l_kubeconfig.KubeConfig.from_dict")
    def test_ip_step_is_skip_ignore_errors_skips_missing(
        self, _from_dict, mock_client, read_config, missing_kube, step_context
    ):
        mock_client.return_value = missing_kube
        step = _IPStep(Mock(), pool_name=None, ignore_errors=True)

        assert step.is_skip(step_context).result_type == ResultType.SKIPPED
