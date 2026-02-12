# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from enum import StrEnum

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import BaseStep, Result, ResultType, SunbeamException
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    MODEL_DELAY,
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.versions import MYSQL_CHARMS_K8S

LOG = logging.getLogger(__name__)

UPGRADE_ALL_UNITS_TIMEOUT = 3600
MYSQL_UPGRADE_CONFIG_KEY = "mysql_k8s_upgrade_state"
MYSQL_CHARM = "mysql-k8s"


class MySQLUpgradeState(StrEnum):
    """Tracks the steps of the mysql-k8s charm upgrade.

    The order of the enum members is significant as it defines the progression
    of the upgrade steps. If the upgrade is interrupted, this order is used
    to figure out where to resume.

    """

    INIT = "init"
    ORIGINAL_STATE_RECORDED = "original_state_recorded"
    SCALED_UP = "scaled_up"
    PRECHECK_DONE = "precheck_done"
    HIGHEST_UNIT_UPGRADED = "highest_unit_upgraded"
    UPGRADE_RESUMED = "upgrade_resumed"
    UNITS_SETTLED = "units_settled"
    SCALED_BACK = "scaled_back"

    def __ge__(self, other):
        """Greater than or equal comparison based on enum order."""
        if not isinstance(other, MySQLUpgradeState):
            return NotImplemented
        ordered = list(MySQLUpgradeState)
        return ordered.index(self) >= ordered.index(other)

    def __lt__(self, other):
        """Less than comparison based on enum order."""
        if not isinstance(other, MySQLUpgradeState):
            return NotImplemented
        ordered = list(MySQLUpgradeState)
        return ordered.index(self) < ordered.index(other)

    def __gt__(self, value):
        """Greater than comparison based on enum order."""
        if not isinstance(value, MySQLUpgradeState):
            return NotImplemented
        ordered = list(MySQLUpgradeState)
        return ordered.index(self) > ordered.index(value)


def load_upgrade_state(client: Client) -> dict:
    """Load persisted mysql upgrade state from clusterd config."""
    state = {}
    try:
        state = json.loads(client.cluster.get_config(MYSQL_UPGRADE_CONFIG_KEY))
    except ConfigItemNotFoundException as e:
        LOG.debug(f"{MYSQL_UPGRADE_CONFIG_KEY} not found: " + str(e))
    except (json.JSONDecodeError, TypeError) as e:
        LOG.warning(f"Found malformed mysql upgrade state from clusterd: {str(e)}. ")
    return state


def write_upgrade_state(client: Client, state: dict):
    """Persist mysql upgrade state to clusterd config."""
    client.cluster.update_config(
        MYSQL_UPGRADE_CONFIG_KEY,
        json.dumps(state),
    )


class MySQLCharmUpgradeStep(BaseStep, JujuStepHelper):
    """Upgrade MySQL K8s charm.

    State transitions:
        INIT -> ORIGINAL_STATE_RECORDED -> SCALED_UP -> PRECHECK_DONE
            -> HIGHEST_UNIT_UPGRADED -> UPGRADE_RESUMED
            -> UNITS_SETTLED -> SCALED_BACK

    Notes:
    - Steps are idempotent: if current state >= target state, the step is a no-op.
    - State is persisted after each successful transition.
    - On failure, the step can be re-run and resumes from the last persisted state.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        application: str = "mysql",
    ):
        super().__init__(
            "MySQL K8s Charm Upgrade",
            f"Refreshing {application} application to latest in-channel charm revision",
        )
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = OPENSTACK_MODEL
        self.application = application
        self.state = MySQLUpgradeState.INIT
        self.original_revision: int | None = None
        self.original_scale: int | None = None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            app = self.jhelper.get_application(self.application, self.model)
        except ApplicationNotFoundException:
            return Result(
                ResultType.SKIPPED,
                "mysql-k8s application has not been deployed yet",
            )
        deployed = app.charm_rev
        if app.base:
            base = f"{app.base.name}@{app.base.channel}"
            latest = self.jhelper.get_available_charm_revision(
                MYSQL_CHARM,
                MYSQL_CHARMS_K8S[MYSQL_CHARM],
                base,
            )
        else:
            LOG.debug("Could not determine base for mysql-k8s.")
            latest = self.jhelper.get_available_charm_revision(
                MYSQL_CHARM,
                MYSQL_CHARMS_K8S[MYSQL_CHARM],
            )

        if deployed == latest:
            LOG.debug("mysql-k8s charm already at latest revision")
            return Result(
                ResultType.SKIPPED,
                f"mysql-k8s already at latest revision {deployed}",
            )

        return Result(ResultType.COMPLETED)

    def _set_state(self, state: MySQLUpgradeState):
        self.state = state
        write_upgrade_state(
            self.client,
            {
                "state": state.name,
                "original_revision": self.original_revision,
                "original_scale": self.original_scale,
            },
        )
        LOG.debug("mysql upgrade state %s", state.name)

    def _reset_state(self):
        LOG.debug("mysql resetting upgrade state to INIT;")
        self.original_revision = None
        self.original_scale = None
        self._set_state(MySQLUpgradeState.INIT)

    def _get_highest_unit(self) -> str:
        """Return the highest ordinal unit name."""
        app = self.jhelper.get_application(self.application, self.model)
        return max(app.units, key=lambda u: int(u.split("/")[-1]))

    def record_original_state(self, status: Status | None = None):
        """Record original deployed revision and scale before triggering refresh."""
        if self.state >= MySQLUpgradeState.ORIGINAL_STATE_RECORDED:
            return

        app = self.jhelper.get_application(self.application, self.model)
        self.original_revision = app.charm_rev
        self.original_scale = app.scale
        LOG.debug(
            f"Recorded original mysql-k8s revision: {self.original_revision}, "
            f"scale: {self.original_scale}"
        )
        self._set_state(MySQLUpgradeState.ORIGINAL_STATE_RECORDED)

    def scale_up(self, status: Status | None = None):
        """Scale up mysql-k8s by 1 before upgrade."""
        if self.state >= MySQLUpgradeState.SCALED_UP:
            return

        if self.original_scale is None:
            self._set_state(MySQLUpgradeState.INIT)
            raise SunbeamException(
                "Original mysql-k8s scale was not recorded before scale-up",
                "Re-run `sunbeam cluster refresh` to restart the upgrade",
            )

        target = self.original_scale + 1
        try:
            self.jhelper.scale_application(self.model, self.application, target)
            self.update_status(
                status, "Preparing mysql for upgrade. Scaling up application..."
            )
            self.jhelper.wait_until_active(self.model, apps=[self.application])
            self._set_state(MySQLUpgradeState.SCALED_UP)
        except (JujuWaitException, TimeoutError) as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"Timed out while waiting for {self.application} to become active "
                f"after scaling to {target} units.\n"
                "Check app status and re-run `sunbeam cluster refresh` "
                "to retry the upgrade."
            ) from exc
        except JujuException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"Failed to scale up {self.application} to {target} units.\n"
                "Check application status and re-run `sunbeam cluster refresh` "
                "to retry the upgrade."
            ) from exc

    def run_precheck(self, status: Status | None = None):
        """Run pre-upgrade check on mysql application leader."""
        if self.state >= MySQLUpgradeState.PRECHECK_DONE:
            return

        try:
            leader = self.jhelper.get_leader_unit(self.application, self.model)
            self.update_status(
                status, f"Running pre-upgrade check on {self.application} leader..."
            )
            self.jhelper.run_action(leader, self.model, "pre-upgrade-check")
            self._set_state(MySQLUpgradeState.PRECHECK_DONE)
        except LeaderNotFoundException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"Unable to determine leader unit for {self.application}.\n"
                "Check application status and re-run `sunbeam cluster refresh` "
                "to retry the upgrade."
            ) from exc
        except JujuException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"pre-upgrade-check action failed on {self.application} leader.\n"
                "Check application status and re-run `sunbeam cluster refresh` "
                "to retry the upgrade."
            ) from exc

    def _wait_for_highest_upgrade(self, highest_unit: str):
        """Waits until the highest ordinal unit reports upgrade completed."""

        def _wait(status):
            unit = status.apps[self.application].units[highest_unit]
            # Unit status when upgrade completed on highest ordinal unit
            return (
                unit.workload_status.current == "maintenance"
                and "upgrade completed" in unit.workload_status.message.lower()
                and unit.juju_status.current == "idle"
            )

        with self.jhelper._model(self.model) as juju:
            self.jhelper._wait(_wait, juju, delay=MODEL_DELAY, timeout=10 * 60)

    def refresh_and_wait_highest(self, status: Status | None = None):
        """Juju refresh application and wait for highest ordinal unit.

        Wait for highest unit to complete upgrade.
        """
        if self.state >= MySQLUpgradeState.HIGHEST_UNIT_UPGRADED:
            return

        try:
            highest = self._get_highest_unit()
            self.update_status(
                status, f"Waiting for highest unit {highest} to complete upgrade..."
            )
            self.jhelper.charm_refresh(self.application, self.model)
            self._wait_for_highest_upgrade(highest)
            self._set_state(MySQLUpgradeState.HIGHEST_UNIT_UPGRADED)
        except (JujuWaitException, TimeoutError) as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                "Timed out waiting for highest unit to complete upgrade.\n"
                " Check unit status and re-run `sunbeam cluster refresh`"
                " to retry the upgrade",
            ) from exc
        except JujuException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"`juju refresh` failed for mysql-k8s on the highest unit: {str(exc)}\n"
                " Check unit status and re-run `sunbeam cluster refresh`"
                " to retry the upgrade",
            ) from exc

    def resume_upgrade(self, status: Status | None = None):
        """Run resume-upgrade action on mysql application leader."""
        if self.state >= MySQLUpgradeState.UPGRADE_RESUMED:
            return

        try:
            self.update_status(
                status, f"Running resume-upgrade action on {self.application} leader..."
            )
            leader = self.jhelper.get_leader_unit(self.application, self.model)
            self.jhelper.run_action(leader, self.model, "resume-upgrade", {})
            self._set_state(MySQLUpgradeState.UPGRADE_RESUMED)
        except LeaderNotFoundException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"No leader found for {self.application} to run `resume-upgrade`.\n"
                " Check app status and re-run `sunbeam cluster refresh`"
                " to retry resume-upgrade",
            ) from exc
        except JujuException as exc:
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"resume-upgrade action failed on {self.application}: {str(exc)}\n"
                " Check app status and re-run `sunbeam cluster refresh`"
                " to retry resume-upgrade",
            ) from exc

    def wait_until_active(self, status: Status | None = None):
        """Wait until all mysql units are active after resume-upgrade."""
        if self.state >= MySQLUpgradeState.UNITS_SETTLED:
            return

        try:
            self.update_status(
                status,
                f"Waiting for {self.application} units to complete upgrade and "
                "settle to active...",
            )
            self.jhelper.wait_until_active(
                model=self.model,
                apps=[self.application],
                timeout=UPGRADE_ALL_UNITS_TIMEOUT,
            )
            self._set_state(MySQLUpgradeState.UNITS_SETTLED)
        except (JujuWaitException, TimeoutError) as exc:
            hint = "\n".join(
                (
                    f"Consider rollback to revision {self.original_revision}, ",
                    "follow the instructions below:",
                    f"  1. Run `juju run {self.application}/leader "
                    "pre-upgrade-check` to configure rollback",
                    f"  2. Run `juju refresh --revision <previous-revision> "
                    f"{self.application}` to initiate the rollback",
                    f"  3. Run `juju run {self.application}/leader "
                    "resume-upgrade` to resume the rollback",
                )
            )
            raise SunbeamException(
                "MySQL upgrade failed: "
                f"Timed out waiting for units to settle to active: {str(exc)}\n"
                f"{hint}"
            ) from exc

    def scale_back(self, status: Status | None = None):
        """Scale back mysql-k8s to the original scale before upgrade started.

        Failing at this step logs a warning but does not fail the overall upgrade.
        """
        if self.state >= MySQLUpgradeState.SCALED_BACK:
            return

        if self.original_scale is None:
            message = (
                f"{self.application} scale-back skipped: original scale is unknown"
            )
            LOG.warning(message)
            self.update_status(status, message)
            return

        app = self.jhelper.get_application(self.application, self.model)
        current_scale = app.scale
        expected_scale = self.original_scale + 1

        if current_scale != expected_scale:
            message = (
                f"{self.application} scale-back skipped: current scale is "
                f"{current_scale}, expected {expected_scale}. "
                f"Cluster scale does not match upgrade assumptions."
            )
            LOG.warning(message)
            self.update_status(status, message)
            return
        try:
            self.update_status(
                status,
                f"{self.application} units completed upgrade. "
                "Scaling back to original scale...",
            )
            self.jhelper.scale_application(
                self.model, self.application, self.original_scale
            )
            self.jhelper.wait_until_active(self.model, apps=[self.application])
            self._set_state(MySQLUpgradeState.SCALED_BACK)
        except (JujuException, JujuWaitException, TimeoutError) as exc:
            LOG.warning(
                f"Upgrade completed but scale-back to original scale: "
                f"{self.original_scale} failed: {str(exc)}",
            )

    def run(self, status: Status | None = None) -> Result:
        """Run mysql-k8s charm upgrade steps."""
        persisted = load_upgrade_state(self.client)
        state_name = persisted.get("state", MySQLUpgradeState.INIT.name)
        try:
            self.state = MySQLUpgradeState[state_name]
        except KeyError:
            LOG.warning(f"Invalid mysql-k8s upgrade state: {state_name}")
            self.state = MySQLUpgradeState.INIT
        self.original_revision = persisted.get("original_revision")
        self.original_scale = persisted.get("original_scale")
        LOG.debug("Starting from mysql upgrade state: %s", self.state.name)

        try:
            self.record_original_state(status)
            self.scale_up(status)
            self.run_precheck(status)
            self.refresh_and_wait_highest(status)
            self.resume_upgrade(status)
            self.wait_until_active(status)
            self.scale_back(status)

            # reset state after successful upgrade
            self._reset_state()

            return Result(
                ResultType.COMPLETED, "mysql-k8s charm upgrade completed successfully"
            )
        except SunbeamException as exc:
            return Result(ResultType.FAILED, str(exc))
