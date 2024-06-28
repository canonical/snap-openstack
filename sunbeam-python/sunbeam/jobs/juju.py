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
import asyncio.queues
import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import (
    Awaitable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    TypedDict,
    TypeVar,
    cast,
)

import pydantic
import pytz
import yaml
from juju import utils as juju_utils
from juju.application import Application
from juju.charmhub import CharmHub
from juju.client import client as juju_client
from juju.controller import Controller
from juju.errors import (
    JujuAPIError,
    JujuError,
)
from juju.machine import Machine
from juju.model import Model
from juju.unit import Unit

from sunbeam.clusterd.client import Client
from sunbeam.versions import JUJU_BASE

LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
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
    """Raised when a query timed out."""

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


class JujuSecretNotFound(JujuException):
    """Raised when secret is missing from model."""

    pass


class ChannelUpdate(TypedDict):
    """Channel Update step.

    Defines a channel that needs updating to and the expected
    state of the charm afterwards.

    channel: Channel to upgrade to
    expected_status: map of accepted statuses for "workload" and "agent"
    """

    channel: str
    expected_status: Dict[str, List[str]]


class JujuAccount(pydantic.BaseModel):
    user: str
    password: str

    def to_dict(self):
        """Return self as dict."""
        return self.model_dump()

    @classmethod
    def load(cls, data_location: Path) -> "JujuAccount":
        """Load account from file."""
        data_file = data_location / ACCOUNT_FILE
        try:
            with data_file.open() as file:
                return JujuAccount(**yaml.safe_load(file))
        except FileNotFoundError as e:
            raise JujuAccountNotFound(
                "Juju user account not found, is node part of sunbeam "
                f"cluster yet? {data_file}"
            ) from e

    def write(self, data_location: Path):
        """Dump self to file."""
        data_file = data_location / ACCOUNT_FILE
        if not data_file.exists():
            data_file.touch()
        data_file.chmod(0o660)
        with data_file.open("w") as file:
            yaml.safe_dump(self.to_dict(), file)


class JujuController(pydantic.BaseModel):
    api_endpoints: List[str]
    ca_cert: str

    def to_dict(self):
        """Return self as dict."""
        return self.model_dump()

    @classmethod
    def load(cls, client: Client) -> "JujuController":
        """Load controller from clusterd."""
        controller = client.cluster.get_config(JUJU_CONTROLLER_KEY)
        return JujuController(**json.loads(controller))

    def write(self, client: Client):
        """Dump self to clusterd."""
        client.cluster.update_config(JUJU_CONTROLLER_KEY, json.dumps(self.to_dict()))

    def to_controller(self, juju_account: JujuAccount) -> Controller:
        """Return connected controller."""
        controller = Controller()
        run_sync(
            controller.connect(
                endpoint=self.api_endpoints,
                cacert=self.ca_cert,
                username=juju_account.user,
                password=juju_account.password,
            )
        )
        return controller


class JujuHelper:
    """Helper function to manage Juju apis through pylibjuju."""

    def __init__(self, controller: Controller):
        self.controller = controller

    async def get_clouds(self) -> dict:
        """Return clouds available on controller."""
        clouds = await self.controller.clouds()
        return clouds.clouds

    async def list_models(self) -> list:
        """List models."""
        models = await self.controller.list_models()
        return models

    async def get_model(self, model: str) -> Model:
        """Fetch model.

        :model: Name of the model
        """
        try:
            return await self.controller.get_model(model)
        except Exception as e:
            if "HTTP 400" in str(e) or "HTTP 404" in str(e):
                raise ModelNotFoundException(f"Model {model!r} not found")
            raise e

    async def add_model(self, model: str, config: dict | None = None) -> Model:
        """Add a model.

        :model: Name of the model
        :config: model configuration
        """
        # TODO(gboutry): workaround until we manage public ssh keys properly
        old_home = os.environ["HOME"]
        os.environ["HOME"] = os.environ["SNAP_REAL_HOME"]
        try:
            return await self.controller.add_model(model, config=config)
        finally:
            os.environ["HOME"] = old_home

    async def integrate(
        self,
        model: str,
        provider: str,
        requirer: str,
        relation: str,
    ):
        """Integrate two applications.

        Does not support different relation names on provider and requirer.

        :model: Name of the model
        :provider: Name of the application providing the relation
        :requirer: Name of the application requiring the relation
        :relation: Name of the relation
        """
        model_impl = await self.get_model(model)
        if requirer not in model_impl.applications:
            raise ApplicationNotFoundException(
                f"Application {requirer!r} is missing from model {model!r}"
            )
        if provider not in model_impl.applications:
            raise ApplicationNotFoundException(
                f"Application {provider!r} is missing from model {model!r}"
            )
        endpoint_fmt = "{app}:{relation}"
        provider_relation = endpoint_fmt.format(app=provider, relation=relation)
        requirer_relation = endpoint_fmt.format(app=requirer, relation=relation)
        await model_impl.integrate(provider_relation, requirer_relation)

    async def are_integrated(
        self, model: str, provider: str, requirer: str, relation: str
    ) -> bool:
        """Check if two applications are integrated.

        Does not support different relation names on provider and requirer.

        :model: Name of the model of the providing app
        :provider: Name of the application providing the relation
        :requirer: Name of the application requiring the relation
        :relation: Name of the relation
        """
        app = await self.get_application(provider, model)
        apps = (provider, requirer)
        for rel in app.relations:
            if len(rel.endpoints) != 2:
                # skip peer relationship
                continue
            left_ep, right_ep = rel.endpoints
            if (
                left_ep.application_name not in apps
                or right_ep.application_name not in apps
            ):
                continue
            if left_ep.name == relation and right_ep.name == relation:
                return True
        return False

    async def get_model_name_with_owner(self, model: str) -> str:
        """Get juju model full name along with owner."""
        model_impl = await self.get_model(model)
        owner = model_impl.info.owner_tag.removeprefix(OWNER_TAG_PREFIX)
        return f"{owner}/{model_impl.info.name}"

    async def get_model_status(
        self, model: str, filter: list[str] | None = None
    ) -> dict:
        """Get juju filtered status."""
        model_impl = await self.get_model(model)
        status = await model_impl.get_status(filter)
        return json.loads(status.to_json())

    async def get_model_status_full(self, model: str) -> dict:
        """Get juju status for the model."""
        return await self.get_model_status(model)

    async def get_application_names(self, model: str) -> List[str]:
        """Get Application names in the model.

        :model: Name of the model
        """
        model_impl = await self.get_model(model)
        return list(model_impl.applications.keys())

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

    async def get_machines(self, model: str) -> dict[str, Machine]:
        """Fetch machines in model.

        :model: Name of the model where the machines are located
        """
        model_impl = await self.get_model(model)
        return model_impl.machines

    async def set_model_config(self, model: str, config: dict) -> None:
        """Set model config for the given model."""
        model_impl = await self.get_model(model)
        await model_impl.set_config(config)

    async def deploy(
        self,
        name: str,
        charm: str,
        model: str,
        num_units: int = 1,
        channel: str | None = None,
        revision: int | None = None,
        to: list[str] | None = None,
        config: dict | None = None,
    ):
        """Deploy an application."""
        options = {}
        if to:
            options["to"] = to
        if channel:
            options["channel"] = channel
        if revision:
            options["revision"] = revision
        if config:
            options["config"] = config

        model_impl = await self.get_model(model)
        await model_impl.deploy(
            charm,
            application_name=name,
            num_units=num_units,
            base=JUJU_BASE,
            **options,
        )

    async def add_machine(self, name: str, model: str) -> Machine:
        """Add machines to model."""
        model_impl = await self.get_model(model)
        machine: Machine = await model_impl.add_machine(
            spec=model_impl.uuid + ":" + name, series="jammy"
        )  # type: ignore
        return machine

    async def get_unit(self, name: str, model: str) -> Unit:
        """Fetch an application's unit in model.

        :name: Name of the unit to wait for, name format is application/id
        :model: Name of the model where the unit is located
        """
        self._validate_unit(name)
        model_impl = await self.get_model(model)

        unit = model_impl.units.get(name)

        if unit is None:
            raise UnitNotFoundException(
                f"Unit {name!r} is missing from model {model!r}"
            )
        return unit

    async def get_unit_from_machine(
        self, application: str, machine_id: str, model: str
    ) -> Unit:
        """Fetch a application's unit in model on a specific machine.

        :application: application name of the unit to look for
        :machine_id: Id of machine unit is on
        :model: Name of the model where the unit is located
        """
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

    async def add_unit(
        self,
        name: str,
        model: str,
        machine: list[str] | str | None = None,
    ) -> list[Unit]:
        """Add unit to application, can be optionnally placed on a machine.

        :name: Application name
        :model: Name of the model where the application is located
        :machine: Machine ID to place the unit on, optional
        """
        application = await self.get_application(name, model)
        if machine is None or isinstance(machine, str):
            count = 1
        else:
            # machine is a list
            count = len(machine)

        # Note(gboutry): add_unit waits for unit to be added to model,
        # but does not check status
        return await application.add_unit(count, machine)

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

    async def run_cmd_on_machine_unit(
        self, name: str, model: str, cmd: str, timeout=None
    ):
        """Run a shell command on a machine unit.

        :name: unit name
        :model: Name of the model where the application is located
        :cmd: Command to run
        :timeout: Timeout in seconds
        :returns: Command results
        """
        unit = await self.get_unit(name, model)
        action = await unit.run(cmd, timeout=timeout, block=True)
        if action.results["return-code"] != 0:
            raise CmdFailedException(action.results["stderr"])
        return action.results

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
        try:
            action = await unit.run(pebble + " " + cmd, timeout=timeout, block=True)
        except asyncio.TimeoutError as e:
            raise TimeoutException(f"Timeout while running command: {cmd}") from e
        if action.results["return-code"] != 0:
            raise CmdFailedException(action.results["stderr"])
        return action.results

    async def run_action(
        self, name: str, model: str, action_name: str, action_params={}
    ) -> Dict:
        """Run action and return the response.

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

    async def get_secret(self, model: str, secret_id: str) -> dict:
        """Get secret from model.

        :model: Name of the model
        :secret_id: Secret ID
        """
        model_impl = await self.get_model(model)
        facade = juju_client.SecretsFacade.from_connection(model_impl.connection())
        secrets = await facade.ListSecrets(
            filter_={"uri": secret_id}, show_secrets=True
        )
        if len(secrets.results) != 1:
            raise JujuSecretNotFound(f"Secret {secret_id!r}")
        return secrets["results"][0].serialize()

    async def scp_from(self, name: str, model: str, source: str, destination: str):
        """Scp files from unit to local.

        :name: Unit name
        :model: Name of the model where the application is located
        :source: source file path in the unit
        :destination: destination file path on local
        """
        unit = await self.get_unit(name, model)
        # NOTE: User, proxy, scp_options left to defaults
        await unit.scp_from(source, destination)

    async def add_k8s_cloud(
        self, cloud_name: str, credential_name: str, kubeconfig: dict
    ):
        """Add k8s cloud to controller."""
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        clusters = {v["name"]: v["cluster"] for v in kubeconfig["clusters"]}
        users = {v["name"]: v["user"] for v in kubeconfig["users"]}

        ctx = contexts.get(kubeconfig.get("current-context"))
        cluster = clusters.get(ctx.get("cluster"))
        user = users.get(ctx.get("user"))

        ep = cluster["server"]
        ca_cert = base64.b64decode(cluster["certificate-authority-data"]).decode(
            "utf-8"
        )

        try:
            cloud = juju_client.Cloud(
                auth_types=["oauth2", "clientcertificate"],
                ca_certificates=[ca_cert],
                endpoint=ep,
                host_cloud_region="k8s/localhost",
                regions=[juju_client.CloudRegion(endpoint=ep, name="localhost")],
                type_="kubernetes",
            )
            cloud = await self.controller.add_cloud(cloud_name, cloud)
        except JujuAPIError as e:
            if "already exists" not in str(e):
                raise e

        if "token" in user:
            cred = juju_client.CloudCredential(
                auth_type="oauth2", attrs={"Token": user["token"]}
            )
        elif "client-certificate-data" in user and "client-key-data" in user:
            client_certificate_data = base64.b64decode(
                user["client-certificate-data"]
            ).decode("utf-8")
            client_key_data = base64.b64decode(user["client-key-data"]).decode("utf-8")
            cred = juju_client.CloudCredential(
                auth_type="clientcertificate",
                attrs={
                    "ClientCertificateData": client_certificate_data,
                    "ClientKeyData": client_key_data,
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

    async def wait_application_ready(
        self,
        name: str,
        model: str,
        accepted_status: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until application is ready.

        The function early exits if the application is missing from the model.

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
            LOG.debug(
                "Waiting for app status to be: {} {}".format(
                    model_impl.applications[name].status, accepted_status
                )
            )
            await model_impl.block_until(
                lambda: model_impl.applications[name].status in accepted_status,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for application {name!r} to be ready"
            ) from e

    async def wait_application_gone(
        self,
        names: List[str],
        model: str,
        timeout: Optional[int] = None,
    ):
        """Block execution until application is gone.

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

    async def wait_model_gone(
        self,
        model: str,
        timeout: Optional[int] = None,
    ):
        """Block execution until model is gone.

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

    async def wait_units_ready(
        self,
        units: Sequence[Unit | str],
        model: str,
        accepted_status: Optional[Dict[str, List[str]]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until unit is ready.

        The function early exits if the unit is missing from the model.

        :units: Name of the units or Unit objects to wait for,
            name format is application/id
        :model: Name of the model where the unit is located
        :accepted status: map of accepted statuses for "workload" and "agent"
        :timeout: Waiting timeout in seconds
        """
        if accepted_status is None:
            accepted_status = {}

        agent_accepted_status = accepted_status.get("agent", ["idle"])
        workload_accepted_status = accepted_status.get("workload", ["active"])

        model_impl = await self.get_model(model)
        unit_list: list[Unit] = []
        if isinstance(units, str):
            units = [units]
        for unit in units:
            if isinstance(unit, str):
                self._validate_unit(unit)
                try:
                    unit = await self.get_unit(unit, model)
                except UnitNotFoundException as e:
                    LOG.debug(str(e))
                    return
            unit_list.append(unit)

        for unit in unit_list:
            LOG.debug(
                f"Unit {unit.name!r} is in status: "
                f"agent={unit.agent_status!r}, workload={unit.workload_status!r}"
            )

        def condition() -> bool:
            """Computes readiness for unit."""
            for unit in unit_list:
                unit: Unit = model_impl.units[unit.name]
                agent_ready = unit.agent_status in agent_accepted_status
                workload_ready = unit.workload_status in workload_accepted_status
                if not agent_ready or not workload_ready:
                    return False
            return True

        try:
            await model_impl.block_until(
                condition,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                "Timed out while waiting for units "
                f"{','.join(unit.name for unit in unit_list)} to be ready"
            ) from e

    async def wait_unit_ready(
        self,
        unit: Unit | str,
        model: str,
        accepted_status: Optional[Dict[str, List[str]]] = None,
        timeout: Optional[int] = None,
    ):
        """Block execution until unit is ready.

        The function early exits if the unit is missing from the model.

        :unit: Name of the unit or Unit object to wait for,
            name format is application/id
        :model: Name of the model where the unit is located
        :accepted status: map of accepted statuses for "workload" and "agent"
        :timeout: Waiting timeout in seconds
        """
        await self.wait_units_ready([unit], model, accepted_status, timeout)

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
                unit.entity_id,
                model,
                accepted_status=accepted_status,
                timeout=timeout,
            )

    async def wait_all_machines_deployed(
        self, model: str, timeout: Optional[int] = None
    ):
        """Block execution until all machines in model are deployed.

        :model: Name of the model to wait for readiness
        :timeout: Waiting timeout in seconds
        """
        model_impl = await self.get_model(model)

        def condition() -> bool:
            """Computes readiness for unit."""
            machines = model_impl.machines
            for machine in machines.values():
                if machine is None or machine.status_message != "Deployed":
                    return False
            return True

        try:
            await model_impl.block_until(
                condition,
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                "Timed out while waiting for machines to be deployed"
            ) from e

    async def wait_until_active(
        self,
        model: str,
        apps: list[str] | None = None,
        timeout: int = 10 * 60,
        queue: asyncio.queues.Queue | None = None,
    ) -> None:
        """Wait for all agents in model to reach idle status.

        :model: Name of the model to wait for readiness
        :apps: Name of the appplication to wait for, if None, wait for all apps
        :timeout: Waiting timeout in seconds
        :queue: Queue to put application names in when they are ready, optional, must
            be sized for the number of applications
        """
        model_impl = await self.get_model(model)
        if apps is None:
            apps = list(model_impl.applications.keys())
        await self.wait_until_desired_status(model, apps, ["active"], timeout, queue)

    @staticmethod
    async def _wait_until_status_coroutine(
        model: Model,
        app: str,
        queue: asyncio.queues.Queue | None = None,
        expected_status: Iterable[str] | None = None,
    ):
        """Worker function to wait for application's units workloads to be active."""
        if expected_status is None:
            expected_status = {"active"}
        else:
            expected_status = set(expected_status)
        try:
            while True:
                status = await model.get_status([app])
                if app not in status.applications:
                    raise ValueError(f"Application {app} not found in status")
                application = status.applications[app]
                units = application.units
                status = {unit.workload_status.status for unit in units.values()}
                # int_ is None on machine models
                unit_count: int | None = application.int_
                unit_count_cond = unit_count is None or len(units) == unit_count
                if (
                    unit_count_cond
                    and len(status) > 0
                    and status.issubset(expected_status)
                ):
                    LOG.debug("Application %r is active", app)
                    # queue is sized for the number of coroutines,
                    # it should never throw QueueFull
                    if queue is not None:
                        queue.put_nowait(app)
                    return
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            LOG.debug("Waiting for %r cancelled", app)

    async def wait_until_desired_status(
        self,
        model: str,
        apps: list[str],
        status: list[str] | None = None,
        timeout: int = 10 * 60,
        queue: asyncio.queues.Queue | None = None,
    ) -> None:
        """Wait for all workloads in model to reach desired status.

        :model: Name of the model to wait for readiness
        :apps: Applications to check the status for
        :status: Desired status list
        :timeout: Waiting timeout in seconds
        """
        if status is None:
            wl_status = {"active"}
        else:
            wl_status = set(status)
        model_impl = await self.get_model(model)
        if queue is not None and queue.maxsize < len(apps):
            raise ValueError("Queue size should be at least the number of applications")
        tasks = []
        for app in apps:
            tasks.append(
                asyncio.create_task(
                    self._wait_until_status_coroutine(
                        model_impl, app, queue, wl_status
                    ),
                    name=app,
                )
            )
        excs = []
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
            )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for model {model!r} to be ready"
            ) from e
        finally:
            for task in tasks:
                task.cancel()
                if task.done() and not task.cancelled() and (ex := task.exception()):
                    LOG.debug("coroutine %r exception: %s", task.get_name(), str(ex))
                    excs.append(ex)

        if excs:
            # python 3.11 use ExceptionGroup
            raise JujuWaitException(
                f"Error while waiting for model {model!r} to be ready", excs
            )

    async def set_application_config(self, model: str, app: str, config: dict):
        """Update application configuration.

        :model: Name of the model to wait for readiness
        :application: Application to update
        :config: Config to be set
        """
        model_impl = await self.get_model(model)
        await model_impl.applications[app].set_config(config)

    async def update_applications_channel(
        self,
        model: str,
        updates: Dict[str, ChannelUpdate],
        timeout: Optional[int] = None,
    ):
        """Upgrade charm to new channel.

        :model: Name of the model to wait for readiness
        :application: Application to update
        :channel: New channel
        """
        LOG.debug(f"Updates: {updates}")
        model_impl = await self.get_model(model)
        timestamp = pytz.UTC.localize(datetime.now())
        LOG.debug(f"Base Timestamp {timestamp}")

        coros = [
            model_impl.applications[app_name].upgrade_charm(channel=config["channel"])
            for app_name, config in updates.items()
        ]
        await asyncio.gather(*coros)

        def condition() -> bool:
            """Computes readiness for unit."""
            statuses = {}
            for app_name, config in updates.items():
                _app = model_impl.applications.get(
                    app_name,
                )
                for unit in _app.units:
                    statuses[unit.entity_id] = bool(unit.agent_status_since > timestamp)
            return all(statuses.values())

        try:
            LOG.debug("Waiting for workload status change")
            await model_impl.block_until(
                condition,
                timeout=timeout,
            )
            LOG.debug("Waiting for units ready")
            for app_name, config in updates.items():
                _app = model_impl.applications.get(
                    app_name,
                )
                for unit in _app.units:
                    await self.wait_unit_ready(
                        unit.entity_id, model, accepted_status=config["expected_status"]
                    )
        except asyncio.TimeoutError as e:
            raise TimeoutException(
                f"Timed out while waiting for model {model!r} to be ready"
            ) from e

    async def get_charm_channel(self, application_name: str, model: str) -> str:
        """Get the charm-channel from a deployed application.

        :param application_list: Name of application
        :param model: Name of model
        """
        _status = await self.get_model_status_full(model)
        status = json.loads(_status.to_json())
        return status["applications"].get(application_name, {}).get("charm-channel")

    async def charm_refresh(self, application_name: str, model: str):
        """Update application to latest charm revision in current channel.

        :param application_list: Name of application
        :param model: Name of model
        """
        app = await self.get_application(application_name, model)
        await app.refresh()

    async def get_available_charm_revision(
        self, model: str, charm_name: str, channel: str
    ) -> int:
        """Find the latest available revision of a charm in a given channel.

        :param model: Name of model
        :param charm_name: Name of charm to look up
        :param channel: Channel to lookup charm in
        """
        model_impl = await self.get_model(model)
        available_charm_data = await CharmHub(model_impl).info(charm_name, channel)
        version = available_charm_data["channel-map"][channel]["revision"]["version"]
        return int(version)

    @staticmethod
    def manual_cloud(cloud_name: str, ip_address: str) -> dict[str, dict]:
        """Create manual cloud definition."""
        cloud_yaml = {"clouds": {}}
        cloud_yaml["clouds"][cloud_name] = {
            "type": "manual",
            "endpoint": ip_address,
        }
        return cloud_yaml

    @staticmethod
    def maas_cloud(cloud: str, endpoint: str) -> dict[str, dict]:
        """Create maas cloud definition."""
        clouds = {"clouds": {}}
        clouds["clouds"][cloud] = {
            "type": "maas",
            "auth-types": ["oauth1"],
            "endpoint": endpoint,
        }
        return clouds

    @staticmethod
    def maas_credential(cloud: str, credential: str, maas_apikey: str):
        """Create maas credential definition."""
        credentials = {"credentials": {}}
        credentials["credentials"][cloud] = {
            credential: {
                "auth-type": "oauth1",
                "maas-oauth": maas_apikey,
            }
        }
        return credentials

    async def get_spaces(self, model: str) -> list[dict]:
        """Get spaces in model."""
        model_impl = await self.get_model(model)
        spaces = await model_impl.get_spaces()
        return [json.loads(space.to_json()) for space in spaces]

    async def add_space(self, model: str, space: str, subnets: list[str]):
        """Add a space to the model."""
        model_impl = await self.get_model(model)
        try:
            await model_impl.add_space(space, subnets)
        except JujuAPIError as e:
            raise JujuException(f"Failed to add space {space!r}: {str(e)}") from e

    async def get_application_bindings(self, model: str, application: str) -> dict:
        """Get endpoint bindings for an application."""
        app = await self.get_application(application, model)
        facade = app._facade()
        try:
            app_config = await facade.Get(application=app.name)
        except JujuError as e:
            raise JujuException(
                f"Failed to get bindings for application {app.name!r}: {str(e)}"
            ) from e
        return app_config.endpoint_bindings

    async def merge_bindings(
        self, model: str, application: str, bindings: dict[str, str]
    ):
        """Update endpoint bindings for an application."""
        app = await self.get_application(application, model)
        app_bindings = await self.get_application_bindings(model, application)

        # Check bindings provided are valid
        charm_endpoints = set(app_bindings.keys())
        unknown_endpoints = set(bindings.keys()) - charm_endpoints
        if unknown_endpoints:
            raise JujuException(
                f"Bindings contain unknown endpoints: {', '.join(unknown_endpoints)}"
            )
        # Check spaces are valid
        spaces = await self.get_spaces(model)
        unknown_spaces = set(bindings.values()) - {space["name"] for space in spaces}
        if unknown_spaces:
            raise JujuException(
                f"Bindings contain unknown spaces: {', '.join(unknown_spaces)}"
            )

        request = [
            {
                "application-tag": app.tag,
                "bindings": bindings,
            }
        ]
        facade = app._facade()
        try:
            await facade.MergeBindings(request)
        except JujuError as e:
            raise JujuException(f"Failed to merge bindings: {str(e)}") from e
