# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationReadiness
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.readiness import WaitForFeatureReadyStep, terraform_readiness


def resource(kind, attributes):
    return {"mode": "managed", "type": kind, "instances": [{"attributes": attributes}]}


def test_intended_integrations_include_missing_consumers():
    helper = Mock()
    helper.pull_state.return_value = {
        "resources": [
            resource(
                "juju_application", {"name": "collector", "units": 2, "machines": None}
            ),
            resource(
                "juju_application", {"name": "ceilometer", "units": 3, "machines": None}
            ),
            resource("juju_application", {"name": "unrelated", "units": 1}),
            resource(
                "juju_integration",
                {
                    "application": [
                        {
                            "name": "collector",
                            "endpoint": "receive-loki-logs",
                            "offer_url": None,
                        },
                        {
                            "name": "ceilometer",
                            "endpoint": "logging",
                            "offer_url": None,
                        },
                    ]
                },
            ),
            resource(
                "juju_integration",
                {
                    "application": [
                        {
                            "name": "collector",
                            "endpoint": "send-loki-logs",
                            "offer_url": None,
                        },
                        {
                            "name": "loki-logging",
                            "endpoint": "logging",
                            "offer_url": "remote/model.loki-logging",
                        },
                    ]
                },
            ),
        ]
    }
    requirements = terraform_readiness(
        helper, ["collector"], integration_apps=["collector"]
    )
    assert set(requirements) == {"collector", "ceilometer"}
    assert requirements["collector"].units == 2
    assert requirements["ceilometer"].units == 3
    assert requirements["ceilometer"].relations == {"logging": {"collector"}}
    assert requirements["collector"].relations == {
        "receive-loki-logs": {"ceilometer"},
        "send-loki-logs": {"loki-logging"},
    }


def test_recorded_machine_placements_and_relaxed_provider_policies():
    helper = Mock()
    helper.pull_state.return_value = {
        "resources": [
            resource(
                "juju_application",
                {"name": "cinder-volume-noha", "units": 1, "machines": ["2"]},
            ),
            resource("juju_application", {"name": "mysql", "units": 3}),
            resource("juju_application", {"name": "traefik", "units": 1}),
        ]
    }
    requirements = terraform_readiness(helper)
    assert requirements["cinder-volume-noha"].machines == ["2"]
    assert requirements["mysql"].agent_status == ["idle", "executing"]
    assert requirements["traefik"].status == ["active", "maintenance"]


def test_state_absence_does_not_drop_required_application():
    helper = Mock()
    helper.pull_state.return_value = {"resources": []}
    assert set(terraform_readiness(helper, ["collector"])) == {"collector"}


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("collector/2 executing"),
        TerraformException("state unavailable"),
        ValueError("placement missing"),
    ],
)
def test_final_step_propagates_failure(error, step_context):
    jhelper = Mock()
    requirements = Mock()
    if isinstance(error, TimeoutError):
        requirements.return_value = {"machines": {"collector": ApplicationReadiness()}}
        jhelper.wait_until_models_ready.side_effect = error
    else:
        requirements.side_effect = error
    result = WaitForFeatureReadyStep(jhelper, requirements, 60).run(step_context)
    assert result.result_type == ResultType.FAILED
    assert result.message == str(error)
