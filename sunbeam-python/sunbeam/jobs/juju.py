# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from functools import wraps
from pathlib import Path
from typing import Awaitable, Dict, List, Optional, TypeVar, cast

import tenacity
import yaml
from juju import utils as juju_utils
from juju.application import Application
from juju.client import client as jujuClient
from juju.controller import Controller
from juju.errors import (
    JujuAgentError,
    JujuAPIError,
    JujuAppError,
    JujuMachineError,
    JujuUnitError,
)
from juju.model import Model
from juju.unit import Unit

from sunbeam.clusterd.client import Client as clusterClient

LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
# Note(gboutry): pylibjuju get_model does not support user/model
MODEL = CONTROLLER_MODEL.split("/")[1]
CONTROLLER = "sunbeam-controller"
JUJU_CONTROLLER_KEY = "JujuController"
ACCOUNT_FILE = "account.yaml"
OWNER_TAG_PREFIX = "user-"


T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    """Helper to run coroutines synchronously."""
    result = asyncio.get_event_loop().run_until_complete(coro)
    return cast(T, result)


class JujuException(Exception):
    """Main juju exception, to be subclassed."""

    pass


class ControllerNotFoundException(JujuException):
    """Raised when controller is missing."""

    pass


class ModelNotFoundException(JujuException):
    """Raised when model is missing."""

    pass


class MachineNotFoundException(JujuException):
    """Raised when machine is missing from model."""

    pass


class JujuAccountNotFound(JujuException):
    """Raised when account in snap's user_data is missing."""

    pass


class ApplicationNotFoundException(JujuException):
    """Raised when application is missing from model."""

    pass


class UnitNotFoundException(JujuException):
    """Raised when unit is missing from model."""

    pass


class LeaderNotFoundException(JujuException):
    """Raised when no unit is designated as leader."""

    pass


class TimeoutException(JujuException):
    """Raised when a query timed out"""

    pass


class ActionFailedException(JujuException):
    """Raised when Juju run failed."""

    pass


class CmdFailedException(JujuException):
    """Raised when Juju run cmd failed."""

    pass


class JujuWaitException(JujuException):
    """Raised for any errors during wait."""

    pass


class UnsupportedKubeconfigException(JujuException):
    """Raised when kubeconfig have unsupported config."""

    pass


@dataclass
class JujuAccount:
    user: str
    password: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, data_location: Path) -> "JujuAccount":
        data_file = data_location / ACCOUNT_FILE
        try:
            with data_file.open() as file:
                return JujuAccount(**yaml.safe_load(file))
        except FileNotFoundError as e:
            raise JujuAccountNotFound(
                "Juju user account not found, is node part of sunbeam cluster yet?"
            ) from e

    def write(self, data_location: Path):
        data_file = data_location / ACCOUNT_FILE
        if not data_file.exists():
            data_file.touch()
        data_file.chmod(0o660)
        with data_file.open("w") as file:
            yaml.safe_dump(self.to_dict(), file)


@dataclass
class JujuController:
    api_endpoints: List[str]
    ca_cert: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, client: clusterClient) -> "JujuController":
        controller = client.cluster.get_config(JUJU_CONTROLLER_KEY)
        return JujuController(**json.loads(controller))

    def write(self, client: clusterClient):
        client.cluster.update_config(JUJU_CONTROLLER_KEY, json.dumps(self.to_dict()))


def controller(func):
    """Automatically set up controller."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if self.controller is None:
            client = clusterClient()
            juju_controller = JujuController.load(client)

            account = JujuAccount.load(self.data_location)

            self.controller = Controller()
            await self.controller.connect(
                endpoint=juju_controller.api_endpoints,
                cacert=juju_controller.ca_cert,
                username=account.user,
                password=account.password,
            )
        return await func(self, *args, **kwargs)

    return wrapper


class JujuHelper:
    """Helper function to manage Juju apis through pylibjuju."""

    def __init__(self, data_location: Path):
        self.data_location = data_location
        self.controller = None

    @controller
    async def get_clouds(self) -> dict:
        clouds = await self.controller.clouds()
        return clouds.clouds

    @controller
    async def get_model(self, model: str) -> Model:
        """Fetch model.

        :model: Name of the model
        """
        try:
            return await self.controller.get_model(model)
        except Exception as e:
            if "HTTP 400" in str(e):
                raise ModelNotFoundException(f"Model {model!r} not found")
            raise e

    @controller
    async def get_model_name_with_owner(self, model: str) -> str:
        """Get juju model full name along with owner"""
        model_impl = await self.get_model(model)
        owner = model_impl.info.owner_tag.removeprefix(OWNER_TAG_PREFIX)
        return f"{owner}/{model_impl.info.name}"

    @controller
    async def get_model_status_full(self, model: str) -> Dict:
        """Get juju status for the model"""
        model_impl = await self.get_model(model)
        status = await model_impl.get_status()
        return status

    @controller
    async def get_application_names(self, model: str) -> List[str]:
        """Get Application names in the model.

        :model: Name of the model
        """
        model_impl = await self.get_model(model)
        return list(model_impl.applications.keys())

    @controller
    async def get_application(self, name: str, model: str) -> Application:
        """Fetch application in model.

        :name: Application name
        :model: Name of the model where the application is located
        """
        model_impl = await self.get_model(model)
        application = model_impl.applications.get(name)
        if application is None:
            raise ApplicationNotFoundException(
                f"Application missing from model: {model!r}"
            )
        return application

    @controller
    async def get_unit(self, name: str, model: str) -> Unit:
        """Fetch an application's unit in model.

        :name: Name of the unit to wait for, name format is application/id
        :model: Name of the model where the unit is located"""
        self._validate_unit(name)
        model_impl = await self.get_model(model)

        unit = model_impl.units.get(name)

        if unit is None:
            raise UnitNotFoundException(
                f"Unit {name!r} is missing from model {model!r}"
            )
        return unit

    @controller
    async def get_unit_from_machine(
        self, application: str, machine_id: int, model: str
    ) -> Unit:
        """Fetch a application's unit in model on a specific machine.

        :application: application name of the unit to look for
        :machine_id: Id of machine unit is on
        :model: Name of the model where the unit is located"""
        model_impl = await self.get_model(model)
        application = model_impl.applications.get(application)
        unit = None
        for u in application.units:
            if machine_id == u.machine.entity_id:
                unit = u
        return unit

    def _validate_unit(self, unit: str):
        """Validate unit name."""
        parts = unit.split("/")
        if len(parts) != 2:
            raise ValueError(
                f"Name {unit!r} has invalid format, "
                "should be a valid unit of format application/id"
            )

    @controller
    async def add_unit(
        self,
        name: str,
        model: str,
        machine: Optional[str] = None,
    ) -> Unit:
        """Add unit to application, can be optionnally placed on a machine.

        :name: Application name
        :model: Name of the model where the application is located
        :machine: Machine ID to place the unit on, optional
        """

        model_impl = await self.get_model(model)

        application = model_impl.applications.get(name)

        if application is None:
            raise ApplicationNotFoundException(
                f"Application {name!r} is missing from model {model!r}"
            )

        # Note(gboutry): add_unit waits for unit to be added to model,
        # but does not check status
        # we add only one unit, so it's ok to get the first result
        return (await application.add_unit(1, machine))[0]

    @controller
    async def remove_unit(self, name: str, unit: str, model: str):
        """Remove unit from application.

        :name: Application name
        :unit: Unit tag
        :model: Name of the model where the application is located
        """
        self._validate_unit(unit)
        model_impl = await self.get_model(model)

        application = model_impl.applications.get(name)

        if application is None:
            raise ApplicationNotFoundException(
                f"Application {name!r} is missing from model {model!r}"
            )

        await application.destroy_unit(unit)

    @controller
    async def get_leader_unit(self, name: str, model: str) -> str:
        """Get leader unit.

        :name: Application name
        :model: Name of the model where the application is located
        :returns: Unit name
        """
        application = await self.get_application(name, model)

        for unit in application.units:
            is_leader = await unit.is_leader_from_status()
            if is_leader:
                return unit.entity_id

        raise LeaderNotFoundException(
            f"Leader for application {name!r} is missing from model {model!r}"
        )

    @controller
    async def run_cmd_on_unit_payload(
        self,
        name: str,
        model: str,
        cmd: str,
        container: str,
        timeout=None,
    ) -> str:
        """Run a shell command on an unit's payload container.

        :name: unit name
        :model: Name of the model where the application is located
        :cmd: Command to run
        :container_name: Name of the payload container to run on
        :timeout: Timeout in seconds
        :returns: Command results
        """

        unit = await self.get_unit(name, model)
        pebble = " ".join(
            [
                f"PEBBLE_SOCKET=/charm/containers/{container}/pebble.socket",
                "/charm/bin/pebble",
                "exec",
                "--",
            ]
        )
        action = await unit.run(pebble + " " + cmd, timeout=timeout, block=True)
        if action.results["return-code"] != 0:
            raise CmdFailedException(action.results["stderr"])
        return action.results

    @controller
    async def run_action(
        self, name: str, model: str, action_name: str, action_params={}
    ) -> Dict:
        """Run action and return the response

        :name: Unit name
        :model: Name of the model where the application is located
        :action: Action name
        :kwargs: Arguments to action
        :returns: dict of action results
        :raises: UnitNotFoundException, ActionFailedException,
                 Exception when action not defined
        """
        model_impl = await self.get_model(model)

        unit = await self.get_unit(name, model)
        action_obj = await unit.run_action(action_name, **action_params)
        await action_obj.wait()
        if action_obj._status != "completed":
            output = await model_impl.get_action_output(action_obj.id)
            raise ActionFailedException(output)

        return action_obj.results

    @controller
    async def scp_from(self, name: str, model: str, source: str, destination: str):
        """scp files from unit to local

        :name: Unit name
        :model: Name of the model where the application is located
        :source: source file path in the unit
        :destination: destination file path on local
        """
        unit = await self.get_unit(name, model)
        # NOTE: User, proxy, scp_options left to defaults
        await unit.scp_from(source, destination)

    @controller
    async def add_k8s_cloud(
        self, cloud_name: str, credential_name: str, kubeconfig: dict
    ):
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        clusters = {v["name"]: v["cluster"] for v in kubeconfig["clusters"]}
        users = {v["name"]: v["user"] for v in kubeconfig["users"]}

        ctx = contexts.get(kubeconfig.get("current-context"))
        cluster = clusters.get(ctx.get("cluster"))
        user = users.get(ctx.get("user"))

        ep = cluster["server"]
        caCert = base64.b64decode(cluster["certificate-authority-data"]).decode("utf-8")

        try:
            cloud = jujuClient.Cloud(
                auth_types=["oauth2", "clientcertificate"],
                ca_certificates=[caCert],
                endpoint=ep,
                host_cloud_region="microk8s/localhost",
                regions=[jujuClient.CloudRegion(endpoint=ep, name="localhost")],
                type_="kubernetes",
            )
            cloud = await self.controller.add_cloud(cloud_name, cloud)
        except JujuAPIError as e:
            if "already exists" not in str(e):
                raise e

        if "token" in user:
            cred = jujuClient.CloudCredential(
                auth_type="oauth2", attrs={"Token": user["token"]}
            )
        elif "client-certificate-data" in user and "client-key-data" in user:
            clientCertificateData = base64.b64decode(
                user["client-certificate-data"]
            ).decode("utf-8")
            clientKeyData = base64.b64decode(user["client-key-data"]).decode("utf-8")
            cred = jujuClient.CloudCredential(
                auth_type="clientcertificate",
                attrs={
                    "ClientCertificateData": clientCertificateData,
                    "ClientKeyData": clientKeyData,
                },
            )
        else:
            LOG.error("No credentials found for user in config")
            raise UnsupportedKubeconfigException(
                "Unsupported user credentials, only OAuth token and ClientCertificate "
                "are supported"
            )

        await self.controller.add_credential(
            credential_name, credential=cred, cloud=cloud_name
        )

    @controller
    async def wait_application_ready(
        self,
        name: str,
        model: str,
        accepted_status: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until application is ready
        The function early exits if the application is missing from the model

        :name: Name of the application to wait for
        :model: Name of the model where the application is located
        :accepted status: List of status acceptable to exit the waiting loop, default:
            ["active"]
        :timeout: Waiting timeout in seconds
        """
        if accepted_status is None:
            accepted_status = ["active"]

        model_impl = await self.get_model(model)

        try:
            application = await self.get_application(name, model)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return

        LOG.debug(f"Application {name!r} is in status: {application.status!r}")

        try:
            await model_impl.block_until(
                lambda: model_impl.applications[name].status in accepted_status,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for application {name!r} to be ready"
            ) from e

    @controller
    async def wait_application_gone(
        self,
        names: List[str],
        model: str,
        timeout: Optional[int] = None,
    ):
        """Block execution until application is gone

        :names: List of application to wait for departure
        :model: Name of the model where the application is located
        :timeout: Waiting timeout in seconds
        """
        model_impl = await self.get_model(model)

        name_set = set(names)
        empty_set = set()
        try:
            await model_impl.block_until(
                lambda: name_set.intersection(model_impl.applications) == empty_set,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                "Timed out while waiting for applications "
                f"{', '.join(name_set)} to be gone"
            ) from e

    @controller
    async def wait_model_gone(
        self,
        model: str,
        timeout: Optional[int] = None,
    ):
        """Block execution until model is gone

        :model: Name of the model
        :timeout: Waiting timeout in seconds
        """

        async def condition():
            models = await self.controller.list_models()
            return model not in models

        try:
            await juju_utils.block_until_with_coroutine(condition, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for model {model} to be gone"
            ) from e

    @controller
    async def wait_unit_ready(
        self,
        name: str,
        model: str,
        accepted_status: Optional[Dict[str, List[str]]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until unit is ready
        The function early exits if the unit is missing from the model

        :name: Name of the unit to wait for, name format is application/id
        :model: Name of the model where the unit is located
        :accepted status: map of accepted statuses for "workload" and "agent"
        :timeout: Waiting timeout in seconds
        """

        if accepted_status is None:
            accepted_status = {}

        agent_accepted_status = accepted_status.get("agent", ["idle"])
        workload_accepted_status = accepted_status.get("workload", ["active"])

        self._validate_unit(name)
        model_impl = await self.get_model(model)

        try:
            unit = await self.get_unit(name, model)
        except UnitNotFoundException as e:
            LOG.debug(str(e))
            return

        LOG.debug(
            f"Unit {name!r} is in status: "
            f"agent={unit.agent_status!r}, workload={unit.workload_status!r}"
        )

        def condition() -> bool:
            """Computes readiness for unit"""
            unit = model_impl.units[name]
            agent_ready = unit.agent_status in agent_accepted_status
            workload_ready = unit.workload_status in workload_accepted_status
            return agent_ready and workload_ready

        try:
            await model_impl.block_until(
                condition,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for unit {name!r} to be ready"
            ) from e

    @controller
    async def wait_all_units_ready(
        self,
        app: str,
        model: str,
        accepted_status: Optional[Dict[str, List[str]]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until all units in an application are ready.

        :app: Name of the app whose units to wait for
        :model: Name of the model where the unit is located
        :accepted status: map of accepted statuses for "workload" and "agent"
        :timeout: Waiting timeout in seconds
        """
        model_impl = await self.get_model(model)
        for unit in model_impl.applications[app].units:
            await self.wait_unit_ready(
                unit.entity_id, model, accepted_status=accepted_status
            )

    @controller
    async def wait_until_active(
        self,
        model: str,
        apps: Optional[list] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """Wait for all agents in model to reach idle status

        :model: Name of the model to wait for readiness
        :timeout: Waiting timeout in seconds
        """
        model_impl = await self.get_model(model)

        stop = tenacity.stop_after_attempt(5)
        if timeout:
            stop |= tenacity.stop_after_delay(timeout)
        start = time.time()

        @tenacity.retry(
            retry=tenacity.retry_if_exception_type(JujuAPIError),
            wait=tenacity.wait_fixed(5),
            stop=stop,
        )
        async def _wait_for_idle(start: float, timeout: Optional[int] = None):
            if timeout:
                now = time.time()
                timeout = timeout - int(now - start)
            try:
                # Wait for all the unit workload status
                # to active and Agent status to idle
                await model_impl.wait_for_idle(
                    apps=apps,
                    status="active",
                    timeout=timeout,
                    raise_on_error=False,
                )
            except (JujuMachineError, JujuAgentError, JujuUnitError, JujuAppError) as e:
                raise JujuWaitException(
                    f"Error while waiting for model {model!r} to be ready: {str(e)}"
                ) from e
            except asyncio.TimeoutError as e:
                raise TimeoutException(
                    f"Timed out while waiting for model {model!r} to be ready"
                ) from e

        await _wait_for_idle(start, timeout)

    @controller
    async def set_application_config(self, model: str, app: str, config: dict):
        """Update application configuration

        :model: Name of the model to wait for readiness
        :application: Application to update
        :config: Config to be set
        """
        model_impl = await self.get_model(model)
        await model_impl.applications[app].set_config(config)
