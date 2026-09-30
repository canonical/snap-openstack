# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Capacity policy for compute drain during upgrade.

During the data-plane phase, compute nodes are drained one at a time.
Preflight verifies that enough nodes are free (no running VMs) to
absorb the drain. A node is "free" if the ``running-guests`` juju action
on its ``openstack-hypervisor`` unit returns an empty list.

Policy is stored in clusterd under the ``upgrade_capacity_policy``
config key as JSON. If absent, defaults apply.
"""

from __future__ import annotations

import json
import logging

import pydantic
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.upgrades.preflight.checks import CheckContext, UpgradeCheck

LOG = logging.getLogger(__name__)

CONFIG_KEY = "upgrade_capacity_policy"

HYPERVISOR_APP = "openstack-hypervisor"


class CapacityPolicy(pydantic.BaseModel):
    """Capacity requirements for a safe upgrade drain.

    Preflight fails if the number of free compute nodes (no running
    VMs) is below either threshold.
    """

    min_free_percentage: int = pydantic.Field(
        default=25,
        description="Minimum percentage of compute nodes that must be free",
    )
    min_free_nodes: int = pydantic.Field(
        default=1,
        description="Absolute minimum number of free compute nodes",
    )


def load_capacity_policy(client: Client) -> CapacityPolicy:
    """Load capacity policy from clusterd, or return defaults.

    :param client: clusterd client
    :returns: CapacityPolicy (from clusterd or defaults)
    """
    try:
        raw = client.cluster.get_config(CONFIG_KEY)
        if raw is None:
            return CapacityPolicy()
        if isinstance(raw, str):
            raw = json.loads(raw)
        return CapacityPolicy.model_validate(raw)
    except Exception:
        LOG.debug("Failed to load capacity policy from clusterd, using defaults")
        return CapacityPolicy()


class CapacityCheck(UpgradeCheck):
    """Verify enough free compute nodes for a safe drain.

    Runs the ``running-guests`` action on every ``openstack-hypervisor``
    unit. A node is free if the action returns an empty list. Fails
    exit 1 if free count or percentage is below the policy.
    """

    def __init__(self, ctx: CheckContext, override: bool = False):
        super().__init__(
            "Check compute capacity for drain",
            "Checking compute capacity for drain",
            exit_code=1,
        )
        self.ctx = ctx
        self.override = override

    def run(self, check_status: Status | None = None) -> bool:
        """Return False if insufficient free compute nodes."""
        if self.override:
            LOG.warning("Capacity policy override — skipping capacity check")
            return True

        policy = load_capacity_policy(self.ctx.client)

        try:
            app = self.ctx.jhelper.get_application(
                HYPERVISOR_APP, self.ctx.machines_model
            )
        except Exception as e:
            self.message = (
                f"Cannot find {HYPERVISOR_APP} in model {self.ctx.machines_model}: {e}"
            )
            return False

        unit_names = list(app.units.keys())
        if not unit_names:
            self.message = (
                f"No {HYPERVISOR_APP} units found in model {self.ctx.machines_model}."
            )
            return False

        free_nodes: list[str] = []
        busy_nodes: list[str] = []
        for unit_name in unit_names:
            try:
                result = self.ctx.jhelper.run_action(
                    unit_name, self.ctx.machines_model, "running-guests"
                )
            except Exception as e:
                self.message = (
                    f"Failed to run running-guests action on {unit_name}: {e}"
                )
                return False
            guests_raw = result.get("result", "[]")
            try:
                guests = (
                    json.loads(guests_raw)
                    if isinstance(guests_raw, str)
                    else (guests_raw or [])
                )
            except (json.JSONDecodeError, TypeError):
                guests = []
            if guests:
                busy_nodes.append(unit_name)
            else:
                free_nodes.append(unit_name)

        total = len(unit_names)
        free_count = len(free_nodes)
        free_pct = (free_count * 100) // total if total else 0

        if free_count < policy.min_free_nodes:
            self.message = (
                f"Insufficient free compute nodes: {free_count} free, "
                f"policy requires at least {policy.min_free_nodes}. "
                "Migrate VMs to free up nodes, or override with "
                "--capacity-policy-override."
            )
            return False

        if free_pct < policy.min_free_percentage:
            self.message = (
                f"Insufficient free compute capacity: {free_pct}% free, "
                f"policy requires at least {policy.min_free_percentage}%. "
                "Migrate VMs to free up nodes, or override with "
                "--capacity-policy-override."
            )
            return False

        return True
