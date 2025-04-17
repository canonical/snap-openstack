# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import asyncio
import asyncio.queues
import base64
import contextlib
import json
import logging
import os
import subprocess
import tempfile
import typing
from collections.abc import Collection
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    AsyncGenerator,
    Awaitable,
    Dict,
    TypedDict,
    TypeVar,
    cast,
)

import pydantic
import yaml
from packaging import version
from snaphelpers import Snap
from websockets import ConnectionClosedError

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.core.common import SunbeamException
from sunbeam.lazy import LazyImport
from sunbeam.versions import JUJU_BASE, SUPPORTED_RELEASE

if TYPE_CHECKING:  # pragma: no cover
    import juju.charmhub as juju_charmhub
    import juju.controller as juju_controller
    import juju.errors as juju_errors
    from juju import utils as juju_utils
    from juju.application import Application

    # import private def only for type checking
    from juju.client import _definitions as juju_def
    from juju.client import client as juju_client
    from juju.client import connection as juju_connection
    from juju.client import connector as juju_connector
    from juju.machine import Machine
    from juju.model import Model
    from juju.unit import Unit
else:
    # Lazy loaded attributes
    juju_utils = LazyImport("juju.utils")
    juju_client = LazyImport("juju.client.client")
    juju_connection = LazyImport("juju.client.connection")
    juju_connector = LazyImport("juju.client.connector")
    juju_controller = LazyImport("juju.controller")
    juju_errors = LazyImport("juju.errors")
    juju_charmhub = LazyImport("juju.charmhub")


LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
CONTROLLER_APPLICATION = "controller"
CONTROLLER = "sunbeam-controller"
JUJU_CONTROLLER_KEY = "JujuController"
ACCOUNT_FILE = "account.yaml"
OWNER_TAG_PREFIX = "user-"


T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    """Helper to run coroutines synchronously."""
    result = asyncio.get_event_loop().run_until_complete(coro)
    return cast(T, result)


class JujuException(SunbeamException):
    """Main juju exception, to be subclassed."""

    pass


class ControllerNotFoundException(JujuException):
    """Raised when controller is missing."""

    pass


class ControllerNotReachableException(JujuException):
    """Raised when controller is not reachable."""

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

    def __init__(self, action_result):
        self.action_result = action_result


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
    expected_status: dict[str, list[str]]


class JujuAccount(pydantic.BaseModel):
    user: str
    password: str

    def to_dict(self):
        """Return self as dict."""
        return self.model_dump(by_alias=True)

    @classmethod
    def load(
        cls, data_location: Path, account_file: str = ACCOUNT_FILE
    ) -> "JujuAccount":
        """Load account from file."""
        data_file = data_location / account_file
        try:
            with data_file.open() as file:
                return JujuAccount(**yaml.safe_load(file))
        except FileNotFoundError as e:
            raise JujuAccountNotFound(
                "Juju user account not found, is node part of sunbeam "
                f"cluster yet? {data_file}"
            ) from e

    def write(self, data_location: Path, account_file: str = ACCOUNT_FILE):
        """Dump self to file."""
        data_file = data_location / account_file
        if not data_file.exists():
            data_file.touch()
        data_file.chmod(0o660)
        with data_file.open("w") as file:
            yaml.safe_dump(self.to_dict(), file)


class JujuController(pydantic.BaseModel):
    name: str
    api_endpoints: list[str]
    ca_cert: str
    is_external: bool

    def to_dict(self):
        """Return self as dict."""
        return self.model_dump(by_alias=True)

    @classmethod
    def load(cls, client: Client) -> "JujuController":
        """Load controller from clusterd."""
        controller = client.cluster.get_config(JUJU_CONTROLLER_KEY)
        return JujuController(**json.loads(controller))

    def write(self, client: Client):
        """Dump self to clusterd."""
        client.cluster.update_config(JUJU_CONTROLLER_KEY, json.dumps(self.to_dict()))

    def to_controller(self, juju_account: JujuAccount) -> "juju_controller.Controller":
        """Return connected controller."""
        controller = juju_controller.Controller()
        run_sync(
            controller.connect(
                endpoint=self.api_endpoints,
                cacert=self.ca_cert,
                username=juju_account.user,
                password=juju_account.password,
            )
        )
        return controller


class _SharedStatusUpdater:
    def __init__(self, jhelper: "JujuHelper", model: str):
        self._jhelper = jhelper
        self._condition = asyncio.Condition()
        self._model = model
        self._model_impl: "Model | None" = None

    async def tick_status(
        self, filter: list[str] | None = None
    ) -> AsyncGenerator["juju_def.FullStatus", None]:
        """Asynchronously yields the status of the Juju model when notified.

        This coroutine continuously waits for the model to be ready and then
        retrieves the status of the Juju model. It yields the status as an
        asynchronous generator.

        :filter: An Optional list of strings to filter the
                status results.
        :yield: Filtered full status of the Juju model.
        """
        while True:
            async with self._condition:
                await self._condition.wait()
                # Coroutine is guaranteed to be notified when model is not None
                model = typing.cast("Model", self._model_impl)
                if not self.is_connected():
                    continue
                try:
                    yield await model.get_status(filter)
                except ConnectionClosedError:
                    LOG.debug(
                        "Connection to model %r lost, reconnecting later.",
                        self._model,
                    )
                except asyncio.CancelledError:
                    break

    async def reconnect_model_and_notify_awaiters(self):
        """Ensure the Juju model is ready for use and notify waiting tasks.

        This method checks the connection status of the Juju controller and model,
        attempting to reconnect if necessary. It handles various connection errors
        and ensures that the model is connected before notifying all waiting tasks.
        """
        name = task.get_name() if (task := asyncio.current_task()) else "unknown"
        async with self._condition:
            if not self.is_connected():
                try:
                    if not self._jhelper.controller.is_connected():
                        self._model_impl = None
                        LOG.debug("%r: controller gone", name)
                        await self._jhelper.reconnect()
                        LOG.debug("%r: controller reconnected", name)
                    if self._model_impl is not None:
                        if not self._model_impl.is_connected():
                            LOG.debug("%r: inner connection gone", name)
                        else:
                            LOG.debug(
                                "%r: inner socket gone, connection status -> %r",
                                name,
                                self._model_impl.connection().monitor.status,
                            )
                            await self._model_impl.disconnect()
                        self._model_impl = None
                    if self._model_impl is None:
                        self._model_impl = await self._jhelper.get_model(self._model)
                        LOG.debug("%r: model connected", name)
                except (
                    juju_connector.NoConnectionException,
                    juju_errors.JujuConnectionError,
                    ConnectionClosedError,
                    PermissionError,
                ):
                    LOG.debug(
                        "%r: controller gone while trying to get model, reconnecting",
                        name,
                    )
                    await self._jhelper.reconnect()
                    LOG.debug("%r: controller reconnected", name)
                except Exception:
                    LOG.debug(
                        "%r: error while ensuring connection, retrying later.",
                        name,
                        exc_info=True,
                    )
            if self._model_impl is not None and self.is_connected():
                self._condition.notify_all()

    def is_connected(self) -> bool:
        """Check if model is connected."""
        return (
            self._model_impl is not None
            and self._model_impl.is_connected()
            and self._model_impl.connection().monitor.status
            == juju_connection.Monitor.CONNECTED
        )

    async def disconnect(self):
        """Disconnect model."""
        if self._model_impl:
            await self._model_impl.disconnect()
            self._jhelper.model_connectors.remove(self._model_impl)
            self._model_impl = None


class JujuHelper:
    """Helper function to manage Juju apis through pylibjuju."""

    def __init__(self, controller: "juju_controller.Controller"):
        self.controller = controller
        self._controller_params = controller.connection().connect_params()
        self.model_connectors: list["Model"] = []

    async def reconnect(self, **kwargs):
        """Force reconnection to the controller."""
        await self.disconnect()
        del self.controller
        self.controller = juju_controller.Controller()
        await self.controller.connect(**kwargs, **self._controller_params)

    async def disconnect(self):
        """Disconnect all connections to juju controller."""
        LOG.debug("Disconnecting juju controller and model connections")
        for model in self.model_connectors:
            await model.disconnect()
        self.model_connectors.clear()
        await self.controller.disconnect()

    async def get_clouds(self) -> dict:
        """Return clouds available on controller."""
        clouds = await self.controller.clouds()
        return clouds.clouds

    async def list_models(self) -> list:
        """List models."""
        models = await self.controller.list_models()
        return models

    async def get_model(self, model: str) -> "Model":
        """Fetch model.

        :model: Name of the model
        """
        try:
            model_impl = await self.controller.get_model(model)
            self.model_connectors.append(model_impl)
            return model_impl
        except Exception as e:
            if "HTTP 400" in str(e) or "HTTP 404" in str(e):
                raise ModelNotFoundException(f"Model {model!r} not found")
            raise e

    @contextlib.asynccontextmanager
    async def get_model_closing(self, model: str) -> AsyncGenerator["Model", None]:
        """Fetch model.

        Allow closing a model after usage.

        Do not use the async context manager from the juju.model.Model
        object. It will try to read current model from the filesystem.

        :model: Name of the model
        """
        model_impl = await self.get_model(model)
        try:
            yield model_impl
        finally:
            await model_impl.disconnect()

    async def model_exists(self, model: str) -> bool:
        """Check if model exists.

        :model: Name of the model
        """
        try:
            async with self.get_model_closing(model):
                return True
        except ModelNotFoundException:
            return False

    async def add_model(
        self,
        model: str,
        cloud: str | None = None,
        credential: str | None = None,
        config: dict | None = None,
    ) -> "Model":
        """Add a model.

        :model: Name of the model
        :cloud: Name of the cloud
        :credential: Name of the credential
        :config: model configuration
        """
        # TODO(gboutry): workaround until we manage public ssh keys properly
        old_home = os.environ["HOME"]
        os.environ["HOME"] = os.environ["SNAP_REAL_HOME"]
        try:
            model_impl = await self.controller.add_model(
                model, cloud_name=cloud, credential_name=credential, config=config
            )
            self.model_connectors.append(model_impl)
            return model_impl
        finally:
            os.environ["HOME"] = old_home

    async def destroy_model(
        self, model: str, destroy_storage: bool = False, force: bool = False
    ):
        """Destroy model.

        :model: Name of the model
        """
        await self.controller.destroy_models(
            model, destroy_storage=destroy_storage, force=force
        )

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
        async with self.get_model_closing(model) as model_impl:
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
        async with self.get_model_closing(model) as model_impl:
            app = await self.get_application(provider, model_impl)
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
        try:
            async with self.get_model_closing(model) as model_impl:
                owner = model_impl.info.owner_tag.removeprefix(OWNER_TAG_PREFIX)
                return f"{owner}/{model_impl.info.name}"
        except Exception as e:
            if "HTTP 400" in str(e) or "HTTP 404" in str(e):
                raise ModelNotFoundException(f"Model {model!r} not found")
            raise e

    async def get_model_status(
        self, model: str, filter: list[str] | None = None
    ) -> dict:
        """Get juju filtered status."""
        async with self.get_model_closing(model) as model_impl:
            status = await model_impl.get_status(filter)
            return json.loads(status.to_json())

    async def get_model_status_full(self, model: str) -> dict:
        """Get juju status for the model."""
        return await self.get_model_status(model)

    async def get_machines_status(self, model: str) -> dict:
        """Get machine status in given model."""
        status = await self.get_model_status(model)
        return status["machines"]

    async def get_application_names(self, model: str) -> list[str]:
        """Get Application names in the model.

        :model: Name of the model
        """
        async with self.get_model_closing(model) as model_impl:
            return list(model_impl.applications.keys())

    async def get_application(self, name: str, model: "Model") -> "Application":
        """Fetch application in model.

        :name: Application name
        :model: Name of the model where the application is located
        """
        application = model.applications.get(name)
        if application is None:
            raise ApplicationNotFoundException(
                f"Application missing from model: {model.name!r}"
            )
        return application

    async def get_machines(self, model: "Model") -> dict[str, "Machine"]:
        """Fetch machines in model.

        :model: Name of the model where the machines are located
        """
        return model.machines

    async def get_machine_interfaces(self, model: str, machine: str) -> dict[str, dict]:
        """Fetch machine interfaces.

        :model: Name of the model where the machine is located
        :machine: id of the machine
        """
        status = await self.get_machines_status(model)
        m_status = status.get(machine)
        if m_status is None:
            raise MachineNotFoundException(
                f"Machine {machine!r} is missing from model {model!r}"
            )
        return m_status["network-interfaces"]

    async def set_model_config(self, model: str, config: dict) -> None:
        """Set model config for the given model."""
        async with self.get_model_closing(model) as model_impl:
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
        base: str = JUJU_BASE,
        series: str = SUPPORTED_RELEASE,
    ):
        """Deploy an application."""
        options: dict = {}
        if to:
            options["to"] = to
        if channel:
            options["channel"] = channel
        if revision:
            options["revision"] = revision
        if config:
            options["config"] = config

        async with self.get_model_closing(model) as model_impl:
            await model_impl.deploy(
                charm,
                application_name=name,
                num_units=num_units,
                base=base,
                series=series,
                **options,
            )

    async def add_machine(
        self, name: str, model: "Model", base: str = JUJU_BASE
    ) -> "Machine":
        """Add machine to model.

        Workaround for https://github.com/juju/python-libjuju/issues/1229
        """
        base_name, base_channel = base.split("@")
        params = juju_client.AddMachineParams(
            placement=juju_client.Placement(scope=model.uuid, directive=name),
            jobs=["JobHostUnits"],
            base=juju_client.Base(channel=base_channel, name=base_name),
        )

        client_facade = juju_client.MachineManagerFacade.from_connection(
            model.connection()
        )
        results = await client_facade.AddMachines(params=[params])
        error = results.machines[0].error
        if error:
            raise juju_errors.JujuError("Error adding machine: %s" % error.message)
        machine_id = results.machines[0].machine

        LOG.debug("Added new machine %s", machine_id)
        return await model._wait_for_new("machine", machine_id)  # type: ignore

    async def get_unit(self, name: str, model: "Model") -> "Unit":
        """Fetch an application's unit in model.

        :name: Name of the unit to wait for, name format is application/id
        :model: Name of the model where the unit is located
        """
        self._validate_unit(name)
        unit = model.units.get(name)

        if unit is None:
            raise UnitNotFoundException(
                f"Unit {name!r} is missing from model {model.name!r}"
            )
        return unit

    async def get_unit_from_machine(
        self, application: str, machine_id: str, model: "Model"
    ) -> "Unit":
        """Fetch a application's unit in model on a specific machine.

        :application: application name of the unit to look for
        :machine_id: Id of machine unit is on
        :model: Model object
        """
        app = await self.get_application(application, model)
        unit: "Unit | None" = None
        for u in app.units:
            if machine_id == u.machine.entity_id:
                unit = u
        if unit is None:
            raise UnitNotFoundException(
                f"Unit for application {application!r} on machine {machine_id!r} "
                f"is missing from model {model.name!r}"
            )
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
        application: "Application",
        machine: list[str] | str | None = None,
    ) -> list["Unit"]:
        """Add unit to application, can be optionnally placed on a machine.

        :name: Application name
        :machine: Machine ID to place the unit on, optional
        """
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
        async with self.get_model_closing(model) as model_impl:
            application = model_impl.applications.get(name)

            if application is None:
                raise ApplicationNotFoundException(
                    f"Application {name!r} is missing from model {model!r}"
                )

            await application.destroy_unit(unit)

    async def _get_leader_unit(self, name: str, model: "Model") -> "Unit":
        """Get leader unit.

        :name: Application name
        :model: Model object
        :returns: Leader Unit object
        :raises: LeaderNotFoundException if no leader is found
        """
        application = await self.get_application(name, model)

        for unit in application.units:
            is_leader = await unit.is_leader_from_status()
            if is_leader:
                return unit

        raise LeaderNotFoundException(
            f"Leader for application {name!r} is missing from model {model.name!r}"
        )

    async def get_leader_unit(self, name: str, model: str) -> str:
        """Get leader unit.

        :name: Application name
        :model: Name of the model where the application is located
        :returns: Unit name
        """
        async with self.get_model_closing(model) as model_impl:
            unit = await self._get_leader_unit(name, model_impl)
            return unit.entity_id

    async def get_leader_unit_machine(self, name: str, model: str) -> str:
        """Get leader unit machine id.

        :name: Application name
        :model: Name of the model where the application is located
        :returns: Machine entity id
        """
        async with self.get_model_closing(model) as model_impl:
            unit = await self._get_leader_unit(name, model_impl)
            return unit.machine.entity_id

    async def run_cmd_on_unit_payload(
        self,
        name: str,
        model: str,
        cmd: str,
        container: str,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Run a shell command on an unit's payload container.

        Returns action results irrespective of the return-code
        in action results.

        :name: unit name
        :model: Name of the model where the application is located
        :cmd: Command to run
        :env: Environment variables to set for the pebble command
        :container_name: Name of the payload container to run on
        :timeout: Timeout in seconds
        :returns: Command results

        Command execution failures are part of the results with
        return-code, stdout, stderr.
        """
        async with self.get_model_closing(model) as model_impl:
            unit = await self.get_unit(name, model_impl)
            pebble_cmd = [
                f"PEBBLE_SOCKET=/charm/containers/{container}/pebble.socket",
                "/charm/bin/pebble",
                "exec",
            ]

            if env:
                env_str = ",".join(f"{k}={v}" for k, v in env.items())
                pebble_cmd.extend(["--env", env_str])
            pebble_cmd.append("--")
            pebble = " ".join(pebble_cmd)

            try:
                action = await unit.run(pebble + " " + cmd, timeout=timeout, block=True)
            except asyncio.TimeoutError as e:
                raise TimeoutException(f"Timeout while running command: {cmd}") from e
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
        async with self.get_model_closing(model) as model_impl:
            unit = await self.get_unit(name, model_impl)
            action_obj = await unit.run_action(action_name, **action_params)
            await action_obj.wait()
            if action_obj._status != "completed":
                output = await model_impl.get_action_output(action_obj.id)
                raise ActionFailedException(output)

            return action_obj.results

    async def add_secret(self, model: str, name: str, data: dict, info: str) -> str:
        """Add secret to the model.

        :model: Name of the model.
        :name: Name of the secret.
        :data: Content to save in the secret.
        """
        data_args = [f"{k}={v}" for k, v in data.items()]
        async with self.get_model_closing(model) as model_impl:
            secret = await model_impl.add_secret(name, data_args, info=info)
            return secret

    async def grant_secret(self, model: str, name: str, application: str):
        """Grant secret access to application.

        :model: Name of the model.
        :name: Name of the secret.
        :application: Name of the application.
        """
        async with self.get_model_closing(model) as model_impl:
            await model_impl.grant_secret(name, application)

    async def get_secret(self, model: str, secret_id: str) -> dict:
        """Get secret from model.

        :model: Name of the model
        :secret_id: Secret ID
        """
        async with self.get_model_closing(model) as model_impl:
            secrets = await model_impl.list_secrets(
                filter={"uri": secret_id}, show_secrets=True
            )
            if len(secrets) != 1:
                raise JujuSecretNotFound(f"Secret {secret_id!r}")
            return secrets[0].serialize()

    async def get_secret_by_name(self, model: str, secret_name: str) -> dict:
        """Get secret from model.

        :model: Name of the model
        :secret_id: Secret Name
        """
        async with self.get_model_closing(model) as model_impl:
            secrets = await model_impl.list_secrets(
                filter={"name": secret_name}, show_secrets=True
            )
            if len(secrets) != 1:
                raise JujuSecretNotFound(f"Secret {secret_name!r}")
            return secrets[0].serialize()

    async def remove_secret(self, model: str, name: str):
        """Remove secret in the model.

        :model: Name of the model.
        :name: Name of the secret.
        """
        async with self.get_model_closing(model) as model_impl:
            await model_impl.remove_secret(name)

    async def scp_from(self, name: str, model: str, source: str, destination: str):
        """Scp files from unit to local.

        :name: Unit name
        :model: Name of the model where the application is located
        :source: source file path in the unit
        :destination: destination file path on local
        """
        async with self.get_model_closing(model) as model_impl:
            unit = await self.get_unit(name, model_impl)
            # NOTE: User, proxy, scp_options left to defaults
            await unit.scp_from(source, destination)

    def _generate_juju_credential(self, user: dict) -> "juju_client.CloudCredential":
        """Geenrate juju crdential object from kubeconfig user."""
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

        return cred

    async def add_k8s_cloud(
        self, cloud_name: str, credential_name: str, kubeconfig: dict
    ):
        """Add k8s cloud to controller."""
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        clusters = {v["name"]: v["cluster"] for v in kubeconfig["clusters"]}
        users = {v["name"]: v["user"] for v in kubeconfig["users"]}

        # TODO(gboutry): parse context with lightkube for better handling
        ctx = contexts.get(kubeconfig.get("current-context", {}), {})
        cluster = clusters.get(ctx.get("cluster", {}), {})
        user = users.get(ctx.get("user"), {})

        if user is None:
            raise UnsupportedKubeconfigException(
                "No user found in current kubeconfig context, cannot add credential"
            )

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
        except juju_errors.JujuAPIError as e:
            if "already exists" not in str(e):
                raise e

        cred = self._generate_juju_credential(user)
        await self.controller.add_credential(
            credential_name, credential=cred, cloud=cloud_name
        )

    async def update_k8s_cloud(self, cloud_name: str, kubeconfig: dict):
        """Update K8S cloud endpoint."""
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        clusters = {v["name"]: v["cluster"] for v in kubeconfig["clusters"]}

        ctx = contexts.get(kubeconfig.get("current-context", {}), {})
        cluster = clusters.get(ctx.get("cluster", {}), {})

        ep = cluster["server"]
        ca_cert = base64.b64decode(cluster["certificate-authority-data"]).decode(
            "utf-8"
        )
        cloud_facade = juju_client.CloudFacade.from_connection(
            self.controller.connection()
        )
        await cloud_facade.UpdateCloud(
            [
                {
                    "cloud": juju_client.Cloud(
                        auth_types=["oauth2", "clientcertificate"],
                        ca_certificates=[ca_cert],
                        endpoint=ep,
                        host_cloud_region="k8s/localhost",
                        regions=[
                            juju_client.CloudRegion(endpoint=ep, name="localhost")
                        ],
                        type_="kubernetes",
                    ),
                    "name": cloud_name,
                }
            ]
        )

    async def add_k8s_credential(
        self, cloud_name: str, credential_name: str, kubeconfig: dict
    ):
        """Add K8S Credential to controller."""
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        users = {v["name"]: v["user"] for v in kubeconfig["users"]}
        ctx = contexts.get(kubeconfig.get("current-context"), {})
        user = users.get(ctx.get("user"), {})

        if user is None:
            raise UnsupportedKubeconfigException(
                "No user found in current kubeconfig context, cannot add credential"
            )
        cred = self._generate_juju_credential(user)
        await self.controller.add_credential(
            credential_name, credential=cred, cloud=cloud_name
        )

    async def wait_application_ready(
        self,
        name: str,
        model: str,
        accepted_status: list[str] | None = None,
        timeout: int | None = None,
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

        async with self.get_model_closing(model) as model_impl:
            application = typing.cast(
                "Application | None", model_impl.applications.get(name)
            )
            if application is None:
                return

            LOG.debug(f"Application {name!r} is in status: {application.status!r}")

            try:
                LOG.debug(
                    "Waiting for app status to be: {} {}".format(
                        application.status, accepted_status
                    )
                )
                await model_impl.block_until(
                    lambda: application.status in accepted_status,
                    timeout=timeout,
                )
            except asyncio.TimeoutError as e:
                raise TimeoutException(
                    f"Timed out while waiting for application {name!r} to be ready"
                ) from e

    async def wait_application_gone(
        self,
        names: list[str],
        model: str,
        timeout: int | None = None,
    ):
        """Block execution until application is gone.

        :names: List of application to wait for departure
        :model: Name of the model where the application is located
        :timeout: Waiting timeout in seconds
        """
        async with self.get_model_closing(model) as model_impl:
            name_set = set(names)
            empty_set: set[str] = set()
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
        timeout: int | None = None,
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

    async def wait_units_gone(
        self,
        names: typing.Sequence[str],
        model: str,
        timeout: int | None = None,
    ):
        """Block execution until units are gone.

        :names: List of units to wait for departure
        :model: Name of the model where the units are located
        :timeout: Waiting timeout in seconds
        """
        async with self.get_model_closing(model) as model_impl:
            name_set = set(names)
            empty_set: set[str] = set()
            try:
                await model_impl.block_until(
                    lambda: name_set.intersection(model_impl.units) == empty_set,
                    timeout=timeout,
                )
            except asyncio.TimeoutError as e:
                raise TimeoutException(
                    "Timed out while waiting for units"
                    f" {', '.join(name_set)} to be gone"
                ) from e

    async def wait_all_machines_deployed(self, model: str, timeout: int | None = None):
        """Block execution until all machines in model are deployed.

        :model: Name of the model to wait for readiness
        :timeout: Waiting timeout in seconds
        """
        async with self.get_model_closing(model) as model_impl:

            def condition() -> bool:
                """Computes readiness for machine."""
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
        async with self.get_model_closing(model) as model_impl:
            if apps is None:
                apps = list(model_impl.applications.keys())
            await self.wait_until_desired_status(
                model, apps, status=["active"], timeout=timeout, queue=queue
            )

    async def _model_ticker(self, shared_updater: _SharedStatusUpdater):
        """Update model every 15 seconds.

        This coroutine continuously updates the model by calling the
        `shared_updater.reconnect_model_and_notify_awaiters` method every 15 seconds.
        It runs indefinitely until cancelled.

        :shared_updater: An instance of
                _SharedStatusUpdater used to ensure the model is ready
                and notify relevant parties.
        """
        try:
            while True:
                try:
                    await shared_updater.reconnect_model_and_notify_awaiters()
                except asyncio.CancelledError:
                    # Prevent silencing CancelledError
                    raise
                except Exception:
                    # silence exception to have the ticker retry
                    LOG.debug(
                        "Model ticker failed to reconnect, retrying.", exc_info=True
                    )
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            LOG.debug("Model ticker cancelled")

    @staticmethod
    def _is_desired_status_achieved(
        application_status: "juju_def.ApplicationStatus",
        unit_list: Collection[str],
        expected_status: Collection[str],
        expected_agent_status: Collection[str] | None = None,
        expected_workload_status_message: Collection[str] | None = None,
    ):
        """Check if the desired status is achieved for the given application.

        :application_status: The status of the application.
        :unit_list: List of unit names to check.
        :expected_status: Expected workload status values.
        :expected_agent_status: Expected agent status values.
        :expected_workload_status_message: Expected workload status messages.
        """
        units = application_status.units
        app_status: set[str] = set()
        agent_status: set[str] = set()
        workload_status_message: set[str] = set()
        # Application is a subordinate, collect status from app instead of units
        # as units is empty dictionary.
        if application_status.subordinate_to:
            if application_status.status:
                app_status = {str(application_status.status.status)}
        else:
            for name, unit in units.items():
                unit = typing.cast("juju_def.UnitStatus", unit)
                if len(unit_list) == 0 or name in unit_list:
                    if wl_status := unit.workload_status:
                        app_status.add(str(wl_status.status))
                        if _wl_status_message := wl_status.info:
                            workload_status_message.add(str(_wl_status_message))
                    if _agent_status := unit.agent_status:
                        agent_status.add(str(_agent_status.status))

        if len(unit_list) == 0:
            # int_ is None on machine models
            expected_unit_count = application_status.int_
            unit_count = len(units)
        else:
            expected_unit_count = len(unit_list)
            unit_count = len([unit for unit in units if unit in unit_list])

        has_expected_unit_count = (
            expected_unit_count is None or expected_unit_count == unit_count
        )
        has_expected_app_status = len(app_status) > 0 and app_status.issubset(
            expected_status
        )
        has_expected_agent_status = (
            expected_agent_status is None
            or agent_status.issubset(expected_agent_status)
        )
        has_expected_workload_status_message = (
            expected_workload_status_message is None
            or len(workload_status_message) == 0  # No status message on workload
            or workload_status_message.issubset(expected_workload_status_message)
        )
        return (
            has_expected_unit_count
            and has_expected_app_status
            and has_expected_agent_status
            and has_expected_workload_status_message
        )

    @staticmethod
    async def _wait_until_status_coroutine(
        shared_updater: _SharedStatusUpdater,
        app: str,
        unit_list: list[str] | None = None,
        queue: asyncio.queues.Queue | None = None,
        expected_status: Collection[str] | None = None,
        expected_agent_status: Collection[str] | None = None,
        expected_workload_status_message: Collection[str] | None = None,
    ):
        """Worker function to wait for application's units workloads to be active.

        This coroutine periodically checks the status of the specified application and
        its units, waiting until the desired status conditions are met.

        :shared_updater: An instance of _SharedStatusUpdater to fetch status updates.
        :app: The name of the application to monitor.
        :unit_list: Optional list of specific units to check within the application.
        :queue: Optional asyncio queue to notify when the application reaches
                the desired status.
        :expected_status: Optional collection of desired application statuses.
        :expected_agent_status: Optional collection of desired agent statuses.
        :expected_workload_status_message: Optional collection of desired workload
                                           status messages.
        """
        expected_status = expected_status or {"active"}
        try:
            async for status in shared_updater.tick_status([app]):
                if app not in status.applications:
                    raise ValueError(f"Application {app} not found in status")
                application_status = typing.cast(
                    "juju_def.ApplicationStatus", status.applications[app]
                )

                if JujuHelper._is_desired_status_achieved(
                    application_status,
                    unit_list or [],
                    expected_status,
                    expected_agent_status,
                    expected_workload_status_message,
                ):
                    LOG.debug("Application %r is active", app)
                    # queue is sized for the number of coroutines,
                    # it should never throw QueueFull
                    if queue is not None:
                        queue.put_nowait(app)
                    break
        except asyncio.CancelledError:
            LOG.debug("Waiting for %r cancelled", app)

    async def wait_until_desired_status(
        self,
        model: str,
        apps: list[str],
        units: list[str] | None = None,
        status: list[str] | None = None,
        agent_status: list[str] | None = None,
        workload_status_message: list[str] | None = None,
        timeout: int = 10 * 60,
        queue: asyncio.queues.Queue | None = None,
    ) -> None:
        """Wait for all workloads in the specified model to reach the desired status.

        :model: Name of the model to wait for readiness.
        :apps: Applications to check the status for.
        :units: Units to check the status for. If None, all units of the
                app will be checked.
        :status: Desired workload status list. If None, defaults to {"active"}.
        :agent_status: Desired agent status list.
        :workload_status_message: List of desired workload status messages.
        :timeout: Waiting timeout in seconds.
        :queue: An asyncio queue to use for status updates. If provided,
                its maxsize should be at least the number of applications.
        """
        if status is None:
            wl_status = {"active"}
        else:
            wl_status = set(status)
        if queue is not None and queue.maxsize < len(apps):
            raise ValueError("Queue size should be at least the number of applications")
        LOG.debug("Waiting for apps %r to be %r", apps, wl_status)
        shared_updater = _SharedStatusUpdater(self, model)
        tasks: list[asyncio.Task] = []
        for app in apps:
            unit_list = (
                None if units is None else [unit for unit in units if app in unit]
            )
            if unit_list:
                LOG.debug(
                    "Waiting for units %r of app %r to be %r",
                    unit_list,
                    app,
                    wl_status,
                )
            tasks.append(
                asyncio.create_task(
                    self._wait_until_status_coroutine(
                        shared_updater,
                        app,
                        unit_list,
                        queue,
                        wl_status,
                        agent_status,
                        workload_status_message,
                    ),
                    name=app,
                )
            )
        updater_task = asyncio.create_task(
            self._model_ticker(shared_updater), name="model-ticker"
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
            updater_task.cancel()
            await shared_updater.disconnect()

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
        async with self.get_model_closing(model) as model_impl:
            await model_impl.applications[app].set_config(config)

    async def get_charm_channel(self, application_name: str, model: str) -> str:
        """Get the charm-channel from a deployed application.

        :param application_list: Name of application
        :param model: Name of model
        """
        status = await self.get_model_status_full(model)
        return status["applications"].get(application_name, {}).get("charm-channel")

    async def charm_refresh(self, application_name: str, model: "Model"):
        """Update application to latest charm revision in current channel.

        :param application_list: Name of application
        :param model: Model object
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
        async with self.get_model_closing(model) as model_impl:
            available_charm_data = await juju_charmhub.CharmHub(model_impl).info(
                charm_name, channel
            )
            version = available_charm_data["channel-map"][channel]["revision"][
                "version"
            ]
            return int(version)

    @staticmethod
    def manual_cloud(cloud_name: str, ip_address: str) -> dict[str, dict]:
        """Create manual cloud definition."""
        cloud_yaml: dict[str, dict] = {"clouds": {}}
        cloud_yaml["clouds"][cloud_name] = {
            "type": "manual",
            "endpoint": ip_address,
            "auth-types": ["empty"],
        }
        return cloud_yaml

    @staticmethod
    def maas_cloud(cloud: str, endpoint: str) -> dict[str, dict]:
        """Create maas cloud definition."""
        clouds: dict[str, dict] = {"clouds": {}}
        clouds["clouds"][cloud] = {
            "type": "maas",
            "auth-types": ["oauth1"],
            "endpoint": endpoint,
        }
        return clouds

    @staticmethod
    def maas_credential(cloud: str, credential: str, maas_apikey: str):
        """Create maas credential definition."""
        credentials: dict[str, dict] = {"credentials": {}}
        credentials["credentials"][cloud] = {
            credential: {
                "auth-type": "oauth1",
                "maas-oauth": maas_apikey,
            }
        }
        return credentials

    @staticmethod
    def empty_credential(cloud: str):
        """Create empty credential definition."""
        credentials: dict[str, dict] = {"credentials": {}}
        credentials["credentials"][cloud] = {
            "empty-creds": {
                "auth-type": "empty",
            }
        }
        return credentials

    async def get_spaces(self, model: str) -> list[dict]:
        """Get spaces in model."""
        async with self.get_model_closing(model) as model_impl:
            spaces = await model_impl.get_spaces()
            return [json.loads(space.to_json()) for space in spaces]

    async def add_space(self, model: str, space: str, subnets: list[str]):
        """Add a space to the model."""
        async with self.get_model_closing(model) as model_impl:
            try:
                await model_impl.add_space(space, subnets)
            except juju_errors.JujuAPIError as e:
                raise JujuException(f"Failed to add space {space!r}: {str(e)}") from e

    async def get_application_bindings(self, model: str, application: str) -> dict:
        """Get endpoint bindings for an application."""
        async with self.get_model_closing(model) as model_impl:
            app = await self.get_application(application, model_impl)
            facade = app._facade()
            try:
                app_config = await facade.Get(application=app.name)
            except juju_errors.JujuError as e:
                raise JujuException(
                    f"Failed to get bindings for application {app.name!r}: {str(e)}"
                ) from e
            return app_config.endpoint_bindings

    async def merge_bindings(
        self, model: str, application: str, bindings: dict[str, str]
    ):
        """Update endpoint bindings for an application."""
        async with self.get_model_closing(model) as model_impl:
            app = await self.get_application(application, model_impl)
            app_bindings = await self.get_application_bindings(model, application)

            # Check bindings provided are valid
            charm_endpoints = set(app_bindings.keys())
            unknown_endpoints = set(bindings.keys()) - charm_endpoints
            if unknown_endpoints:
                raise JujuException(
                    "Bindings contain unknown endpoints:"
                    f" {', '.join(unknown_endpoints)}"
                )
            # Check spaces are valid
            spaces = await self.get_spaces(model)
            unknown_spaces = set(bindings.values()) - {
                space["name"] for space in spaces
            }
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
            except juju_errors.JujuError as e:
                raise JujuException(f"Failed to merge bindings: {str(e)}") from e


class JujuStepHelper:
    jhelper: JujuHelper

    def _get_juju_binary(self) -> str:
        """Get juju binary path."""
        snap = Snap()
        juju_binary = snap.paths.snap / "juju" / "bin" / "juju"
        return str(juju_binary)

    def _juju_cmd(self, *args):
        """Runs the specified juju command line command.

        The command will be run using the json formatter. Invoking functions
        do not need to worry about the format or the juju command that should
        be used.

        For example, to run the juju bootstrap k8s, this method should
        be invoked as:

          self._juju_cmd('bootstrap', 'k8s')

        Any results from running with json are returned after being parsed.
        Subprocess execution errors are raised to the calling code.

        :param args: command to run
        :return:
        """
        cmd = [self._get_juju_binary()]
        cmd.extend(args)
        cmd.extend(["--format", "json"])

        LOG.debug(f"Running command {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")

        return json.loads(process.stdout.strip())

    def get_clouds(
        self, cloud_type: str, local: bool = False, controller: str | None = None
    ) -> list:
        """Get clouds based on cloud type.

        If local is True, return clouds registered in client.
        If local is False, return clouds registered in client and controller.
        If local is False and controller specified, return clouds registered
        in controller.
        """
        clouds = []
        cmd = ["clouds"]
        if local:
            cmd.append("--client")
        else:
            if controller:
                cmd.extend(["--controller", controller])
        clouds_from_juju_cmd = self._juju_cmd(*cmd)
        LOG.debug(f"Available clouds in juju are {clouds_from_juju_cmd.keys()}")

        for name, details in clouds_from_juju_cmd.items():
            if details["type"] == cloud_type:
                clouds.append(name)

        LOG.debug(f"There are {len(clouds)} {cloud_type} clouds available: {clouds}")

        return clouds

    def get_credentials(
        self, cloud: str | None = None, local: bool = False
    ) -> dict[str, dict]:
        """Get credentials."""
        cmd = ["credentials"]
        if local:
            cmd.append("--client")
        if cloud:
            cmd.append(cloud)
        return self._juju_cmd(*cmd)

    def get_controllers(self, clouds: list | None = None) -> list:
        """Get controllers hosted on given clouds.

        if clouds is None, return all the controllers.
        """
        controllers = self._juju_cmd("controllers")
        controllers = controllers.get("controllers", {}) or {}
        if clouds is None:
            return list(controllers.keys())

        existing_controllers = [
            name for name, details in controllers.items() if details["cloud"] in clouds
        ]
        LOG.debug(
            f"There are {len(existing_controllers)} existing {clouds} "
            f"controllers running: {existing_controllers}"
        )
        return existing_controllers

    def get_external_controllers(self) -> list:
        """Get all external controllers registered."""
        snap = Snap()
        data_location = snap.paths.user_data
        external_controllers = []

        controllers = self.get_controllers()
        for controller in controllers:
            account_file = data_location / f"{controller}.yaml"
            if account_file.exists():
                external_controllers.append(controller)

        return external_controllers

    def get_controller(self, controller: str) -> dict:
        """Get controller definition."""
        try:
            return self._juju_cmd("show-controller", controller)[controller]
        except subprocess.CalledProcessError as e:
            LOG.debug(e)
            raise ControllerNotFoundException() from e

    def get_controller_ip(self, controller: str) -> str:
        """Get Controller IP of given juju controller.

        Returns Juju Controller IP.
        Raises ControllerNotFoundException or ControllerNotReachableException.
        """
        controller_details = self.get_controller(controller)
        endpoints = controller_details.get("details", {}).get("api-endpoints", [])
        controller_ip_port = utils.first_connected_server(endpoints)
        if not controller_ip_port:
            raise ControllerNotReachableException(
                f"Juju Controller {controller} not reachable"
            )

        controller_ip = controller_ip_port.rsplit(":", 1)[0]
        return controller_ip

    def add_cloud(self, name: str, cloud: dict, controller: str | None) -> bool:
        """Add cloud to client clouds.

        If controller is specified, add cloud to both client
        and given controller.
        """
        if cloud["clouds"][name]["type"] not in ("manual", "maas"):
            return False

        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(cloud).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-cloud",
                name,
                "--file",
                temp.name,
                "--client",
            ]
            if controller:
                cmd.extend(["--controller", controller, "--force"])
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

        return True

    def add_k8s_cloud_in_client(self, name: str, kubeconfig: dict):
        """Add k8s cloud in juju client."""
        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(kubeconfig).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-k8s",
                name,
                "--client",
                "--region=localhost/localhost",
            ]

            env = os.environ.copy()
            env.update({"KUBECONFIG": temp.name})
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd, capture_output=True, text=True, check=True, env=env
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

    def add_credential(self, cloud: str, credential: dict, controller: str | None):
        """Add credentials to client or controller.

        If controller is specidifed, credential is added to controller.
        If controller is None, credential is added to client.
        """
        with tempfile.NamedTemporaryFile() as temp:
            temp.write(yaml.dump(credential).encode("utf-8"))
            temp.flush()
            cmd = [
                self._get_juju_binary(),
                "add-credential",
                cloud,
                "--file",
                temp.name,
            ]
            if controller:
                cmd.extend(["--controller", controller])
            else:
                cmd.extend(["--client"])
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )

    def integrate(
        self,
        model: str,
        provider: str,
        requirer: str,
        ignore_error_if_exists: bool = True,
    ):
        """Juju integrate applications."""
        cmd = [
            self._get_juju_binary(),
            "integrate",
            "-m",
            model,
            provider,
            requirer,
        ]
        try:
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True, check=True)
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.debug(e.stderr)
            if ignore_error_if_exists and "already exists" not in e.stderr:
                raise e

    def remove_relation(self, model: str, provider: str, requirer: str):
        """Juju remove relation."""
        cmd = [
            self._get_juju_binary(),
            "remove-relation",
            "-m",
            model,
            provider,
            requirer,
        ]
        LOG.debug(f"Running command {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        LOG.debug(f"Command finished. stdout={process.stdout}, stderr={process.stderr}")

    def revision_update_needed(
        self, application_name: str, model: str, status: dict | None = None
    ) -> bool:
        """Check if a revision update is available for an applicaton.

        :param application_name: Name of application to check for updates for
        :param model: Model application is in
        :param status: Dictionay of model status
        """
        if not status:
            status = run_sync(self.jhelper.get_model_status_full(model))
        app_status = status["applications"].get(application_name, {})
        if not app_status:
            LOG.debug(f"{application_name} not present in model")
            return False
        deployed_revision = int(self._extract_charm_revision(app_status["charm"]))
        charm_name = self._extract_charm_name(app_status["charm"])
        deployed_channel = self.normalise_channel(app_status["charm-channel"])
        if len(deployed_channel.split("/")) > 2:
            LOG.debug(f"Cannot calculate upgrade for {application_name}, branch in use")
            return False
        available_revision = run_sync(
            self.jhelper.get_available_charm_revision(
                model, charm_name, deployed_channel
            )
        )
        return bool(available_revision > deployed_revision)

    def get_charm_deployed_versions(self, model: str) -> dict:
        """Return charm deployed info for all the applications in model.

        For each application, return a tuple of charm name, channel and revision.
        Example output:
        {"keystone": ("keystone-k8s", "2023.2/stable", 234)}
        """
        status = run_sync(self.jhelper.get_model_status_full(model))

        apps = {}
        for app_name, app_status in status.get("applications", {}).items():
            charm_name = self._extract_charm_name(app_status["charm"])
            deployed_channel = self.normalise_channel(app_status["charm-channel"])
            deployed_revision = int(self._extract_charm_revision(app_status["charm"]))
            apps[app_name] = (charm_name, deployed_channel, deployed_revision)

        return apps

    def get_apps_filter_by_charms(self, model: str, charms: list) -> list:
        """Return apps filtered by given charms.

        Get all apps from the model and return only the apps deployed with
        charms in the provided list.
        """
        deployed_all_apps = self.get_charm_deployed_versions(model)
        return [
            app_name
            for app_name, (charm, channel, revision) in deployed_all_apps.items()
            if charm in charms
        ]

    def normalise_channel(self, channel: str) -> str:
        """Expand channel if it is using abbreviation.

        Juju supports abbreviating latest/{risk} to {risk}. This expands it.

        :param channel: Channel string to normalise
        """
        if channel in ["stable", "candidate", "beta", "edge"]:
            channel = f"latest/{channel}"
        return channel

    def _extract_charm_name(self, charm_url: str) -> str:
        """Extract charm name from charm url.

        :param charm_url: Url to examine
        """
        # XXX There must be a better way. ch:amd64/jammy/cinder-k8s-50 -> cinder-k8s
        return charm_url.split("/")[-1].rsplit("-", maxsplit=1)[0]

    def _extract_charm_revision(self, charm_url: str) -> str:
        """Extract charm revision from charm url.

        :param charm_url: Url to examine
        """
        return charm_url.split("-")[-1]

    def channel_update_needed(self, channel: str, new_channel: str) -> bool:
        """Compare two channels and see if the second is 'newer'.

        :param current_channel: Current channel
        :param new_channel: Proposed new channel
        """
        risks = ["stable", "candidate", "beta", "edge"]
        current_channel = self.normalise_channel(channel)
        current_track, current_risk = current_channel.split("/")
        new_track, new_risk = new_channel.split("/")
        if current_track != new_track:
            try:
                return version.parse(current_track) < version.parse(new_track)
            except version.InvalidVersion:
                LOG.error("Error: Could not compare tracks")
                return False
        if risks.index(current_risk) < risks.index(new_risk):
            return True
        else:
            return False

    def get_model_name_with_owner(self, model: str) -> str:
        """Return model name with owner name.

        :param model: Model name

        Raises ModelNotFoundException if model does not exist.
        """
        model_with_owner = run_sync(self.jhelper.get_model_name_with_owner(model))

        return model_with_owner

    def check_secret_exists(self, model_name, secret_name) -> bool:
        """Check if secret exists.

        :return: True if secret exists in the model, False otherwise
        """
        try:
            run_sync(self.jhelper.get_secret_by_name(model_name, secret_name))
            return True
        except JujuSecretNotFound:
            return False


class JujuActionHelper:
    @staticmethod
    def get_unit(
        client: Client, jhelper: JujuHelper, model: str, node: str, app: str
    ) -> "Unit":
        """Retrieve the unit associated with the given node.

        Args:
            client: The Juju client instance.
            jhelper: The JujuHelper instance.
            model: The model name.
            node: The node name.
            app: The application name.

        Returns:
            Unit: The unit associated with the node.
        """
        node_info = client.cluster.get_node_info(node)
        machine_id = str(node_info.get("machineid"))

        juju_model = run_sync(jhelper.get_model(model))
        return run_sync(jhelper.get_unit_from_machine(app, machine_id, juju_model))

    @staticmethod
    def run_action(
        client: Client,
        jhelper: JujuHelper,
        model: str,
        node: str,
        app: str,
        action_name: str,
        action_params: dict[str, typing.Any],
    ) -> Dict:
        """Run the specified action on the unit and return the result.

        Args:
            client: The Juju client instance.
            jhelper: The JujuHelper instance.
            model: The model name.
            node: The node name.
            app: The application name.
            action_name: The name of the action to run.
            action_params: Parameters to pass to the action.

        Returns:
            Dict: The result of the action.

        Raises:
            UnitNotFoundException: If the unit cannot be found.
            ActionFailedException: If the action execution fails.
        """
        try:
            unit = JujuActionHelper.get_unit(client, jhelper, model, node, app)
            LOG.debug(
                "Running action '%s' on unit '%s', params: %s",
                action_name,
                unit.entity_id,
                action_params,
            )

            action_result = run_sync(
                jhelper.run_action(
                    unit.entity_id,
                    model,
                    action_name,
                    action_params=action_params,
                )
            )
            return action_result
        except UnitNotFoundException as e:
            LOG.debug(f"Application {app} not found on node {node}")
            raise e
        except ActionFailedException as e:
            LOG.debug("Action '%s' failed on node '%s': %s", action_name, node, e)
            raise e
