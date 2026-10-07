# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Final readiness checks for feature enablement."""

from collections.abc import Callable, Collection, Iterator

from sunbeam.core.common import BaseStep, Result, ResultType, StepContext
from sunbeam.core.juju import ApplicationReadiness, JujuException, JujuHelper
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.steps.openstack import build_overlay_dict


def _managed_resources(state: dict) -> Iterator[tuple[str, dict]]:
    """Yield current managed resources, including those inside modules."""
    for resource in state.get("resources", []):
        if resource.get("mode") != "managed":
            continue
        for instance in resource.get("instances", []):
            if not instance.get("deposed"):
                yield resource["type"], instance["attributes"]


def terraform_readiness(
    tfhelper: TerraformHelper,
    apps: Collection[str] | None = None,
    integration_apps: Collection[str] = (),
) -> dict[str, ApplicationReadiness]:
    """Read desired local applications and integrations from applied state.

    Only names, placements, counts and endpoint names are extracted. Remote
    offers are not treated as locally managed applications. Existing provider
    overlays remain explicit exceptions to strict workload/agent readiness.
    """
    state = tfhelper.pull_state()
    result = {app: ApplicationReadiness() for app in apps or ()}
    applications = {}
    integrations = []
    for resource_type, attributes in _managed_resources(state):
        if resource_type == "juju_application":
            applications[attributes["name"]] = attributes
            if apps is None:
                result.setdefault(attributes["name"], ApplicationReadiness())
        elif resource_type == "juju_integration":
            endpoints = attributes["application"]
            if any(endpoint.get("name") in integration_apps for endpoint in endpoints):
                integrations.append(endpoints)
                for endpoint in endpoints:
                    if endpoint.get("name") and not endpoint.get("offer_url"):
                        result.setdefault(endpoint["name"], ApplicationReadiness())
    for app, requirement in result.items():
        attributes = applications.get(app, {})
        machines = attributes.get("machines")
        if machines is not None:
            requirement.machines = [str(machine) for machine in machines]
        requirement.units = attributes.get("units")
        overlay = build_overlay_dict([app]).get(app, {})
        requirement.status = overlay.get("status") or requirement.status
        requirement.agent_status = (
            overlay.get("agent_status") or requirement.agent_status
        )
    _require_integrations(result, integrations)
    if not result:
        raise ValueError("No applications found in Terraform state for readiness")
    return result


def _require_integrations(
    requirements: dict[str, ApplicationReadiness], integrations: list[list[dict]]
) -> None:
    """Require every intended endpoint on the locally managed applications."""
    for endpoints in integrations:
        for endpoint in endpoints:
            app = endpoint.get("name")
            if app not in requirements or endpoint.get("offer_url"):
                continue
            for peer in endpoints:
                if peer is not endpoint and peer.get("name"):
                    requirements[app].relations.setdefault(
                        endpoint["endpoint"], set()
                    ).add(peer["name"])


class WaitForFeatureReadyStep(BaseStep):
    """Check the complete affected scope after all enablement mutations."""

    def __init__(
        self,
        jhelper: JujuHelper,
        requirements: Callable[[], dict[str, dict[str, ApplicationReadiness]]],
        timeout: int,
    ):
        super().__init__("Wait for feature readiness", "Waiting for feature readiness")
        self.jhelper = jhelper
        self.requirements = requirements
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Freeze intended targets and wait for complete passing rounds."""
        try:
            self.jhelper.wait_until_models_ready(
                self.requirements(),
                self.timeout,
                progress=lambda message: self.update_status(context, message),
            )
        except (JujuException, TerraformException, TimeoutError, ValueError) as exc:
            return Result(ResultType.FAILED, str(exc))
        return Result(ResultType.COMPLETED)
