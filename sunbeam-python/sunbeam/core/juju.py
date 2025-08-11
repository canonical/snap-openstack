# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import base64
import contextlib
import functools
import ipaddress
import json
import logging
import os
import queue
import subprocess
import tempfile
import time
import typing
from collections.abc import Collection, Generator, Mapping
from pathlib import Path
from typing import (
    Callable,
    TypedDict,
    TypeVar,
)

import jubilant
import jubilant.statustypes
import pydantic
import tenacity
import yaml
from packaging import version
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.core.common import SunbeamException
from sunbeam.versions import JUJU_BASE

LOG = logging.getLogger(__name__)
CONTROLLER_MODEL = "admin/controller"
CONTROLLER_APPLICATION = "controller"
CONTROLLER = "sunbeam-controller"
JUJU_CONTROLLER_KEY = "JujuController"
ACCOUNT_FILE = "account.yaml"
OWNER_TAG_PREFIX = "user-"

MODEL_DELAY = 5

T = TypeVar("T")


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


class ActionFailedException(JujuException):
    """Raised when Juju run failed."""

    def __init__(self, action_result):
        self.action_result = action_result


class ExecFailedException(JujuException):
    """Raised when Juju exec failed."""


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


class JujuHelper:
    """Helper function to manage Juju apis through pylibjuju."""

    def __init__(self, controller: JujuController | None):
        if controller is None:
            raise ValueError("Controller cannot be None")
        self.controller: str = controller.name
        self._juju = jubilant.Juju()

    def cli(
        self,
        *args: str,
        include_controller: bool = True,
        json_format: bool = True,
        juju: "jubilant.Juju | None" = None,
        **kwargs,
    ):
        """Run juju cli command."""
        control_args: list[str] = []

        juju = juju or self._juju

        if include_controller:
            control_args.extend(("--controller", self.controller))
        if json_format:
            control_args.extend(("--format", "json"))
        args = (args[0],) + tuple(control_args) + args[1:]
        ret = juju.cli(*args, **kwargs)
        if json_format:
            try:
                return json.loads(ret)
            except json.JSONDecodeError as e:
                raise CmdFailedException(f"Failed to parse JSON output: {e}") from e
        return ret

    @contextlib.contextmanager
    def _model(self, model: str) -> Generator["jubilant.Juju"]:
        """Context manager to set model for juju commands."""
        _model = self.get_model(model)["name"]  # ensure model is long name
        old_model = self._juju.model
        self._juju.model = _model
        try:
            yield self._juju
        finally:
            self._juju.model = old_model

    def get_clouds(self) -> dict:
        """Return clouds available on controller."""
        return typing.cast(dict, self.cli("clouds", include_model=False))

    def models(self) -> list[dict]:
        """Return list of models on controller."""
        try:
            models: dict = self.cli("models", "--all", include_model=False)
        except jubilant.CLIError as e:
            raise JujuException(e.stderr)
        return models.get("models", [])

    @functools.cache
    def get_model(self, model: str) -> "dict":
        """Fetch model.

        :model: Name of the model
        """
        for m in self.models():
            if model in (m["short-name"], m["name"], m["model-uuid"]):
                return m
        raise ModelNotFoundException(f"Model {model!r} not found")

    def model_exists(self, model: str) -> bool:
        """Check if model exists.

        :model: Name of the model
        """
        try:
            self.get_model(model)
        except JujuException:
            return False
        return True

    def add_model(
        self,
        model: str,
        cloud: str | None = None,
        credential: str | None = None,
        config: dict | None = None,
    ):
        """Add a model.

        :model: Name of the model
        :cloud: Name of the cloud
        :credential: Name of the credential
        :config: model configuration
        """
        self._juju.add_model(model, cloud=cloud, credential=credential, config=config)

    def destroy_model(
        self, model: str, destroy_storage: bool = False, force: bool = False
    ):
        """Destroy model.

        :model: Name of the model
        :destroy_storage: Whether to destroy storage
        :force: Whether to force destroy the model
        """
        try:
            _model = self.get_model(model)
            self._juju.destroy_model(
                _model["name"], destroy_storage=destroy_storage, force=force
            )
        except ModelNotFoundException:
            LOG.debug("Model %s not found", model)

    def integrate(
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
        with self._model(model) as juju:
            status = juju.status()
            if requirer not in status.apps:
                raise ApplicationNotFoundException(
                    f"Application {requirer!r} is missing from model {model!r}"
                )
            if provider not in status.apps:
                raise ApplicationNotFoundException(
                    f"Application {provider!r} is missing from model {model!r}"
                )
            juju.integrate(provider + ":" + relation, requirer + ":" + relation)

    def are_integrated(
        self, model: str, provider: str, requirer: str, relation: str
    ) -> bool:
        """Check if two applications are integrated.

        Only check using the relation name on the provider side.

        :model: Name of the model of the providing app
        :provider: Name of the application providing the relation
        :requirer: Name of the application requiring the relation
        :relation: Name of the relation
        """
        app = self.get_application(provider, model)
        relations = app.relations.get(relation)
        if not relations:
            return False
        for rel in relations:
            if rel.related_app == requirer:
                return True

        return False

    def get_model_name_with_owner(self, model: str) -> str:
        """Get juju model full name along with owner."""
        return self.get_model(model)["name"]

    @tenacity.retry(
        retry=tenacity.retry_if_exception_type(ControllerNotReachableException),
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=10),
        stop=tenacity.stop_after_attempt(8),
    )
    def get_model_status(self, model: str) -> "jubilant.Status":
        """Get juju filtered status."""
        with self._model(model) as juju:
            try:
                return juju.status()
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise ModelNotFoundException(f"Model {model!r} not found")
                if "connection is shut down" in e.stderr:
                    raise ControllerNotReachableException(
                        f"Controller {self.controller!r} is not reachable, "
                    ) from e
                raise JujuException(e.stderr)

    def get_application_names(self, model: str) -> list[str]:
        """Get Application names in the model.

        :model: Name of the model
        """
        return list(self.get_model_status(model).apps.keys())

    def get_application(
        self, name: str, model: str
    ) -> "jubilant.statustypes.AppStatus":
        """Fetch application in model.

        :name: Application name
        :model: Name of the model where the application is located
        """
        application = self.get_model_status(model).apps.get(name)
        if application is None:
            raise ApplicationNotFoundException(
                f"Application missing from model: {model!r}"
            )
        return application

    def get_machines(
        self, model: str
    ) -> "dict[str, jubilant.statustypes.MachineStatus]":
        """Fetch machines in model.

        :model: Name of the model where the machines are located
        """
        return self.get_model_status(model).machines

    def get_machine_interfaces(
        self, model: str, machine: str
    ) -> dict[str, "jubilant.statustypes.NetworkInterface"]:
        """Fetch machine interfaces.

        :model: Name of the model where the machine is located
        :machine: id of the machine
        """
        machines = self.get_machines(model)
        machine_status = machines.get(machine)
        if machine_status is None:
            raise MachineNotFoundException(
                f"Machine {machine!r} is missing from model {model!r}"
            )
        return machine_status.network_interfaces

    def set_model_config(self, model: str, config: dict) -> None:
        """Set model config for the given model."""
        with self._model(model) as juju:
            juju.model_config(config)

    def deploy(
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
    ):
        """Deploy an application."""
        with self._model(model) as juju:
            juju.deploy(
                charm,
                app=name,
                channel=channel,
                revision=revision,
                config=config,
                num_units=num_units,
                base=base,
                to=to,
            )

    def remove_application(
        self, *name: str, model: str, destroy_storage: bool = False, force: bool = False
    ) -> None:
        """Destroy application in model."""
        with self._model(model) as juju:
            juju.remove_application(*name, destroy_storage=destroy_storage, force=force)

    def add_machine(self, name: str, model: str, base: str = JUJU_BASE) -> str:
        """Add machine to model.

        Workaround for https://github.com/juju/python-libjuju/issues/1229
        """
        with self._model(model) as juju:
            output, stderr = juju._cli("add-machine", "--base", base, name)
            machine_id = stderr.strip().split(" ")[-1]
            LOG.debug("Added new machine %s", machine_id)
            return machine_id

    def get_unit(self, name: str, model: str) -> "jubilant.statustypes.UnitStatus":
        """Fetch an application's unit in model.

        :name: Name of the unit to wait for, name format is application/id
        :model: Name of the model where the unit is located
        """
        self._validate_unit(name)
        status = self.get_model_status(model)  # Ensure model exists
        app = status.apps.get(name.split("/")[0])
        if app is None:
            raise ApplicationNotFoundException(
                f"Application {name!r} is missing from model {model!r}"
            )
        unit = app.units.get(name)

        if unit is None:
            raise UnitNotFoundException(
                f"Unit {name!r} is missing from model {model!r}"
            )
        return unit

    def get_unit_from_machine(
        self, application: str, machine_id: str, model: str
    ) -> str:
        """Fetch a application's unit in model on a specific machine.

        :application: application name of the unit to look for
        :machine_id: Id of machine unit is on
        :model: Name of the model where the unit is located
        """
        app = self.get_application(application, model)
        unit = None
        for name, u in app.units.items():
            if machine_id == u.machine:
                unit = name
        if unit is None:
            raise UnitNotFoundException(
                f"Unit for application {application!r} on machine {machine_id!r} "
                f"is missing from model {model!r}"
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

    def add_unit(
        self,
        model: str,
        application: str,
        machines: list[str],
    ) -> list[str]:
        """Add unit to application placed on a machine.

        :model: Name of the model where the application is located
        :name: Application name
        :machines: Machine ID to place the unit on
        """
        if not machines:
            raise ValueError("Machine cannot be empty")
        num_units = len(machines)

        old_app = self.get_application(application, model)
        with self._model(model) as juju:
            juju.add_unit(application, num_units=num_units, to=machines)
        new_app = self.get_application(application, model)

        # note(gboutry): Since Jubilant, we don't know which unit was added
        # by the call
        # Diff the previous application units status
        # Also match on the machine ID in case of multiple nodes
        # joining in local mode
        new_units = []
        for unit, unit_stat in new_app.units.items():
            if unit not in old_app.units and unit_stat.machine in machines:
                LOG.debug(
                    "Added new unit %s for application %s on machine %s",
                    unit,
                    application,
                    unit_stat.machine,
                )
                new_units.append(unit)
        if len(new_units) != num_units:
            raise JujuException(
                f"Failed to add {num_units} units "
                f"to application {application!r} in model "
                f"{model!r}, only {len(new_units)} were added"
            )
        return new_units

    def remove_unit(self, name: str, unit: str, model: str):
        """Remove unit from application.

        :name: Application name
        :unit: Unit tag
        :model: Name of the model where the application is located
        """
        self._validate_unit(unit)
        with self._model(model) as juju:
            juju.remove_unit(unit)

    def _get_leader_unit(
        self, name: str, model: str
    ) -> tuple[str, "jubilant.statustypes.UnitStatus"]:
        """Get leader unit.

        :name: Application name
        :model: Model object
        :returns: Leader Unit name and object
        :raises: LeaderNotFoundException if no leader is found
        """
        application = self.get_application(name, model)

        for unit, status in application.units.items():
            if status.leader:
                return unit, status

        raise LeaderNotFoundException(
            f"Leader for application {name!r} is missing from model {model!r}"
        )

    def get_leader_unit(self, name: str, model: str) -> str:
        """Get leader unit.

        :name: Application name
        :model: Name of the model where the application is located
        :returns: Unit name
        """
        return self._get_leader_unit(name, model)[0]

    def get_leader_unit_machine(self, name: str, model: str) -> str:
        """Get leader unit machine id.

        :name: Application name
        :model: Name of the model where the application is located
        :returns: Machine entity id
        """
        return self._get_leader_unit(name, model)[1].machine

    def run_cmd_on_machine_unit_payload(
        self,
        name: str,
        model: str,
        cmd: str,
        timeout: int | None = None,
    ) -> "jubilant.Task":
        """Run a shell command on a machine unit.

        Returns action results irrespective of the return-code
        in action results.

        :name: unit name
        :model: Name of the model where the application is located
        :cmd: Command to run
        :timeout: Timeout in seconds
        :returns: Command results

        Command execution failures are part of the results with
        return-code, stdout, stderr.
        """
        with self._model(model) as juju:
            try:
                task = juju.exec(cmd, unit=name, wait=timeout)
            except jubilant.TaskError as e:
                raise ExecFailedException(
                    f"Failed to run command {cmd!r} on unit"
                    f" {name!r} in model {model!r}: {e}"
                ) from e
        return task

    def run_cmd_on_unit_payload(
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
        :container: Name of the payload container to run on
        :timeout: Timeout in seconds
        :returns: Command results

        Command execution failures are part of the results with
        return-code, stdout, stderr.
        """
        self._validate_unit(name)
        args: list[str] = []

        args.extend(("exec", "--format", "json", "--unit", name))

        if timeout:
            args.extend(("--wait", f"{timeout}s"))
        args.append("--")
        pebble_socket = f"PEBBLE_SOCKET=/charm/containers/{container}/pebble.socket"
        pebble_path = "/charm/bin/pebble"
        args.extend(("env", pebble_socket, pebble_path, "exec"))
        if env:
            args.extend(f"--env={k}={v}" for k, v in env.items())

        with self._model(model) as juju:
            try:
                stdout, _ = juju._cli(*args, "--", *(cmd.split()), log=False)
            except jubilant.CLIError as e:
                stdout = e.stdout
        return json.loads(stdout)[name]["results"]

    def run_action(
        self,
        name: str,
        model: str,
        action_name: str,
        action_params: dict | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Run action and return the response.

        :name: Unit name
        :model: Name of the model where the application is located
        :action: Action name
        :action_params: Arguments to action
        :timeout: Timeout in seconds
        :returns: Action results
        :raises: UnitNotFoundException, ActionFailedException,
                 Exception when action not defined
        """
        if timeout is None:
            timeout = 1800
        with self._model(model) as juju:
            try:
                task = juju.run(name, action_name, action_params, wait=timeout)
            except jubilant.CLIError as e:
                raise ActionFailedException(str(e))
        if not task.success:
            raise ActionFailedException(str(task))
        return task.results

    def add_secret(self, model: str, name: str, data: dict, info: str) -> str:
        """Add secret to the model.

        :model: Name of the model.
        :name: Name of the secret.
        :data: Content to save in the secret.
        ":info: Information about the secret, e.g. "password for db".
        """
        with self._model(model) as juju:
            return juju.add_secret(name, data, info=info).unique_identifier

    def grant_secret(self, model: str, name: str, application: str):
        """Grant secret access to application.

        :model: Name of the model.
        :name: Name of the secret.
        :application: Name of the application.
        """
        with self._model(model) as juju:
            try:
                juju.cli("grant-secret", name, application)
            except jubilant.CLIError as e:
                raise JujuException(
                    f"Failed to grant secret {name!r} to application {application!r} "
                    f"in model {model!r}: {e.stderr}"
                ) from e

    def get_secret(self, model: str, secret_id: str) -> dict:
        """Get secret from model.

        :model: Name of the model
        :secret_id: Secret ID
        """
        with self._model(model) as juju:
            try:
                secrets: dict = json.loads(
                    juju.cli("show-secret", "--format", "json", "--reveal", secret_id)
                )
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise JujuSecretNotFound(f"Secret {secret_id!r} not found") from e
                raise JujuException(
                    f"Failed to get secret {secret_id!r} from model {model!r}"
                ) from e
        return list(secrets.values())[0]["content"]["Data"]

    def get_secret_by_name(self, model: str, secret_name: str) -> dict:
        """Get secret from model.

        :model: Name of the model
        :secret_id: Secret Name
        """
        return self.get_secret(model, secret_name)

    def remove_secret(self, model: str, name: str):
        """Remove secret in the model.

        :model: Name of the model.
        :name: Name of the secret.
        """
        with self._model(model) as juju:
            juju.cli("remove-secret", name)

    def get_app_config(self, app: str, model: str) -> Mapping:
        """Get the config vaule for an application.

        :app: Name of the application.
        :model: Name of the model.
        """
        with self._model(model) as juju:
            try:
                config_value: Mapping = juju.config(app)
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise ApplicationNotFoundException(f"App {app!r} not found") from e
                raise JujuException(
                    f"Failed to get config {config_value!r} from application {app!r}"
                ) from e
        return config_value

    def _generate_juju_credential(self, user: dict) -> dict:
        """Generate juju credential object from kubeconfig user."""
        if "token" in user:
            cred = {
                "auth-type": "oauth2",
                "Token": user["token"],
            }
        elif "client-certificate-data" in user and "client-key-data" in user:
            client_certificate_data = base64.b64decode(
                user["client-certificate-data"]
            ).decode("utf-8")
            client_key_data = base64.b64decode(user["client-key-data"]).decode("utf-8")
            cred = {
                "auth-type": "clientcertificate",
                "ClientCertificateData": client_certificate_data,
                "ClientKeyData": client_key_data,
            }
        else:
            LOG.error("No credentials found for user in config")
            raise UnsupportedKubeconfigException(
                "Unsupported user credentials, only OAuth token and ClientCertificate "
                "are supported"
            )

        return cred

    def add_k8s_cloud(self, cloud_name: str, credential_name: str, kubeconfig: dict):
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

        cloud = {
            "auth-types": ["oauth2", "clientcertificate"],
            "ca-certificates": [ca_cert],
            "endpoint": ep,
            "host-cloud-region": "k8s/localhost",
            "regions": {
                "localhost": {
                    "endpoint": ep,
                }
            },
            "type": "kubernetes",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as cloud_file:
            cloud_file.write(yaml.safe_dump({"clouds": {cloud_name: cloud}}))
            cloud_file.flush()
            self.cli(
                "add-cloud",
                cloud_name,
                "-f",
                cloud_file.name,
                include_model=False,
                json_format=False,
            )

        cred = self._generate_juju_credential(user)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as cred_file:
            cred_file.write(
                yaml.safe_dump({"credentials": {cloud_name: {credential_name: cred}}})
            )
            cred_file.flush()
            self.cli(
                "add-credential",
                cloud_name,
                "-f",
                cred_file.name,
                include_model=False,
                json_format=False,
            )

    def update_k8s_cloud(self, cloud_name: str, kubeconfig: dict):
        """Update K8S cloud endpoint."""
        contexts = {v["name"]: v["context"] for v in kubeconfig["contexts"]}
        clusters = {v["name"]: v["cluster"] for v in kubeconfig["clusters"]}

        ctx = contexts.get(kubeconfig.get("current-context", {}), {})
        cluster = clusters.get(ctx.get("cluster", {}), {})

        ep = cluster["server"]
        ca_cert = base64.b64decode(cluster["certificate-authority-data"]).decode(
            "utf-8"
        )

        clouds = {
            cloud_name: {
                "type": "kubernetes",
                "auth_types": ["oauth2", "clientcertificate"],
                "ca_certificates": [ca_cert],
                "endpoint": ep,
                "host_cloud_region": "k8s/localhost",
                "regions": [
                    {
                        "endpoint": ep,
                        "name": "localhost",
                    }
                ],
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as cloud_file:
            cloud_file.write(yaml.safe_dump(clouds))
            cloud_file.flush()
            self.cli(
                "update-cloud",
                cloud_name,
                "-f",
                cloud_file.name,
                include_model=False,
                json_format=False,
            )

    def add_k8s_credential(
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as cred_file:
            cred_file.write(
                yaml.safe_dump({"credentials": {cloud_name: {credential_name: cred}}})
            )
            cred_file.flush()
            self.cli(
                "add-credential",
                cloud_name,
                "-f",
                cred_file.name,
                include_model=False,
                json_format=False,
            )

    def _wait(
        self,
        ready: Callable[["jubilant.statustypes.Status"], bool],
        juju: "jubilant.Juju",
        *,
        error: Callable[["jubilant.statustypes.Status"], bool] | None = None,
        delay: float = 1.0,
        timeout: float | None = None,
        successes: int = 3,
    ):
        """Retry status until ready or timeout.

        Juju CLI can lose connection to the controller, especially in local mode
        embedded controller, while joining multiple nodes at the same time.
        """
        if timeout is None:
            timeout = 300
        start = time.monotonic()

        while (time.monotonic() - start) < timeout:
            time_elapsed = time.monotonic() - start
            try:
                juju.wait(
                    ready,
                    error=error,
                    delay=delay,
                    timeout=timeout - time_elapsed,
                    successes=successes,
                )
                break
            except jubilant.CLIError as e:
                LOG.error(f"Error occurred while waiting: {e}")
        else:
            raise TimeoutError(
                f"Timed out after {timeout} seconds while waiting for status"
            )

    def wait_application_ready(
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

        def _ready_callback(status: "jubilant.statustypes.Status") -> bool:
            app = status.apps[name]
            return app.app_status.current in accepted_status

        with self._model(model) as juju:
            app = juju.status().apps.get(name)
            if not app:
                return
            LOG.debug(f"Application {name!r} is in status: {app.app_status.current!r}")
            LOG.debug(
                "Waiting for app status to be: {} {}".format(
                    app.app_status.current, accepted_status
                )
            )
            self._wait(_ready_callback, juju, delay=MODEL_DELAY, timeout=timeout)

    def wait_app_endpoint_gone(
        self,
        names: list[str],
        model: str,
        timeout: int | None = None,
    ):
        """Block execution until an application endpoint is gone.

        This function can be used to wait for an application endpoint to be
        removed when a SAAS app is removed from a model. When removing a SAAS,
        if there are any integration to it, it might take a while for those
        relations to be removed, in which time the application endpoint may
        still be present in the model.

        :names: List of application endpoints to wait for to dissapear
        :model: Name of the model where the application endpoint is located
        :timeout: Waiting timeout in seconds
        """
        name_set = set(names)

        def _gone(status: "jubilant.statustypes.Status") -> bool:
            """Check if applications are gone."""
            return len(name_set.intersection(status.app_endpoints)) == 0

        with self._model(model) as juju:
            self._wait(_gone, juju, delay=MODEL_DELAY, timeout=timeout)

    def wait_application_gone(
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
        name_set = set(names)

        def _gone(status: "jubilant.statustypes.Status") -> bool:
            """Check if applications are gone."""
            return len(name_set.intersection(status.apps)) == 0

        with self._model(model) as juju:
            self._wait(_gone, juju, delay=MODEL_DELAY, timeout=timeout)

    def wait_model_gone(
        self,
        model: str,
        timeout: int | None = None,
    ):
        """Block execution until model is gone.

        :model: Name of the model
        :timeout: Waiting timeout in seconds
        """
        if timeout is None:
            timeout = 60 * 15

        start = time.monotonic()
        while self.model_exists(model):
            if time.monotonic() - start > timeout:
                raise TimeoutError(
                    f"Timed out while waiting for model {model!r} to be gone"
                )
            time.sleep(MODEL_DELAY)

    def wait_units_gone(
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
        app_units: dict[str, list[str]] = {}
        for name in names:
            app_units.setdefault(name.split("/")[0], []).append(name)

        def _unit_gones(
            status: "jubilant.statustypes.Status",
        ) -> bool:
            """Check if units are gone."""
            for app, units in app_units.items():
                if app not in status.apps:
                    continue
                name_set = set(units)
                if len(name_set.intersection(status.apps[app].units)) > 0:
                    return False
            return True

        with self._model(model) as juju:
            self._wait(_unit_gones, juju, delay=MODEL_DELAY, timeout=timeout)

    def wait_all_machines_deployed(self, model: str, timeout: int | None = None):
        """Block execution until all machines in model are deployed.

        :model: Name of the model to wait for readiness
        :timeout: Waiting timeout in seconds
        """

        def _machines_deployed(status: "jubilant.statustypes.Status") -> bool:
            """Computes readiness for machine."""
            for machine in status.machines.values():
                if machine.machine_status.message != "Deployed":
                    return False
            return True

        with self._model(model) as juju:
            self._wait(
                _machines_deployed,
                juju,
                delay=MODEL_DELAY,
                timeout=timeout,
            )

    def wait_until_active(
        self,
        model: str,
        apps: list[str] | None = None,
        timeout: int = 10 * 60,
        queue: queue.Queue | None = None,
    ) -> None:
        """Wait for all agents in model to reach idle status.

        :model: Name of the model to wait for readiness
        :apps: Name of the appplication to wait for, if None, wait for all apps
        :timeout: Waiting timeout in seconds
        :queue: Queue to put application names in when they are ready, optional, must
            be sized for the number of applications
        """
        with self._model(model) as juju:
            if apps is None:
                apps = list(juju.status().apps.keys())
            self.wait_until_desired_status(
                model, apps, status=["active"], timeout=timeout, queue=queue
            )

    @staticmethod
    def _is_desired_status_achieved(
        application_status: "jubilant.statustypes.AppStatus",
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
            app_status = {str(application_status.app_status.current)}
        else:
            for name, unit in units.items():
                if len(unit_list) == 0 or name in unit_list:
                    if unit.workload_status.current:
                        app_status.add(unit.workload_status.current)
                    if unit.workload_status.message:
                        workload_status_message.add(unit.workload_status.message)
                    if unit.juju_status.current:
                        agent_status.add(unit.juju_status.current)

        if len(unit_list) == 0:
            # scale is 0 on machine models
            expected_unit_count = application_status.scale
            unit_count = len(units)
        else:
            expected_unit_count = len(unit_list)
            unit_count = len([unit for unit in units if unit in unit_list])

        has_expected_unit_count = (
            expected_unit_count == 0 or expected_unit_count == unit_count
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

    def wait_until_desired_status(
        self,
        model: str,
        apps: list[str],
        units: list[str] | None = None,
        status: list[str] | None = None,
        agent_status: list[str] | None = None,
        workload_status_message: list[str] | None = None,
        timeout: int = 10 * 60,
        queue: queue.Queue | None = None,
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
        :queue: An queue to use for status updates. If provided,
                its maxsize should be at least the number of applications.
        """
        if status is None:
            wl_status = {"active"}
        else:
            wl_status = set(status)
        if queue is not None and queue.maxsize < len(apps):
            raise ValueError("Queue size should be at least the number of applications")
        LOG.debug("Waiting for apps %r to be %r", apps, wl_status)
        app_params = {}
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

            app_params[app] = (
                unit_list or [],
                wl_status,
                agent_status,
                workload_status_message,
            )

        def _wait_until_status(status: "jubilant.statustypes.Status"):
            """Check if all applications are in the desired status."""
            ready = True
            for app, (
                unit_list,
                expected_status,
                expected_agent_status,
                expected_workload_status_message,
            ) in app_params.copy().items():
                if JujuHelper._is_desired_status_achieved(
                    status.apps[app],
                    unit_list,
                    expected_status,
                    expected_agent_status,
                    expected_workload_status_message,
                ):
                    LOG.debug(
                        "Application %r is in expected status %r",
                        app,
                        expected_status,
                    )
                    if queue is not None:
                        queue.put_nowait(app)
                    app_params.pop(app, None)
                else:
                    ready = False
            return ready

        with self._model(model) as juju:
            self._wait(
                _wait_until_status,
                juju,
                delay=MODEL_DELAY,
                timeout=timeout,
            )

    def charm_refresh(self, application_name: str, model: str):
        """Update application to latest charm revision in current channel.

        :param application_list: Name of application
        :param model: Model object
        """
        with self._model(model) as juju:
            juju.refresh(application_name)

    def get_available_charm_revision(
        self, charm_name: str, channel: str, base: str = JUJU_BASE
    ) -> int:
        """Find the latest available revision of a charm in a given channel.

        :param charm_name: Name of charm to look up
        :param channel: Channel to lookup charm in
        :param base: Base to lookup charm in, default is JUJU_BASE
        """
        track, risk = channel.split("/")
        base_name, base_channel = base.split("@")
        output = json.loads(
            self._juju.cli(
                "info",
                "--format",
                "json",
                "--channel",
                channel,
                charm_name,
                include_model=False,
            )
        )

        for risk_info in output["channels"][track][risk]:
            for base_info in risk_info["bases"]:
                if base_info["channel"] == base_channel:
                    return risk_info["revision"]

        raise JujuException(
            f"Could not find charm {charm_name!r} in channel {channel!r} "
            f"with base {base!r}"
        )

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

    def get_spaces(self, model: str) -> list[dict]:
        """Get spaces in model."""
        with self._model(model) as juju:
            return json.loads(juju.cli("spaces", "--format", "json"))["spaces"]

    def add_space(self, model: str, space: str, subnets: list[str]):
        """Add a space to the model."""
        with self._model(model) as juju:
            try:
                juju.cli("add-space", space, *subnets)
            except jubilant.CLIError as e:
                raise JujuException(f"Failed to add space {space!r}: {str(e)}") from e

    def get_space_networks(
        self, model: str, space: str
    ) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """Get networks in a space."""
        with self._model(model) as juju:
            try:
                space_def: dict = json.loads(
                    juju.cli("show-space", "--format", "json", space)
                )
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise JujuException(f"Space {space!r} not found in model {model!r}")
                raise JujuException(f"Failed to get space {space!r}: {str(e)}") from e

        cidrs = []

        for subnet in space_def["space"]["subnets"]:
            try:
                cidrs.append(ipaddress.ip_network(subnet["cidr"]))
            except ValueError as e:
                raise JujuException(
                    f"Invalid network {subnet['cidr']!r} in space {space!r}: {str(e)}"
                ) from e

        return cidrs

    def consume_offer(self, model: str, offer_url: str, alias: str = ""):
        """Consume an offer.

        This function allows the consumtion of an offer with an alias.
        """
        args = [
            offer_url,
        ]
        if alias:
            args.append(alias)
        with self._model(model) as juju:
            try:
                juju.cli("consume", *args)
            except jubilant.CLIError as e:
                raise JujuException(
                    f"Failed to consume oofer {offer_url}: {str(e)}"
                ) from e

    def remove_saas(self, model: str, *saas_name: str):
        """Remove a SaaS application from the model."""
        with self._model(model) as juju:
            try:
                juju.cli("remove-saas", *saas_name)
            except jubilant.CLIError as e:
                raise JujuException(
                    f"Failed to remove SaaS {saas_name!r}: {str(e)}"
                ) from e


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

    def get_charm_deployed_versions(self, model: str) -> dict:
        """Return charm deployed info for all the applications in model.

        For each application, return a tuple of charm name, channel and revision.
        Example output:
        {"keystone": ("keystone-k8s", "2023.2/stable", 234)}
        """
        status = self.jhelper.get_model_status(model)

        apps = {}
        for app_name, app_status in status.apps.items():
            charm_name = app_status.charm_name
            deployed_channel = self.normalise_channel(app_status.charm_channel)
            deployed_revision = app_status.charm_rev
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
        model_with_owner = self.jhelper.get_model_name_with_owner(model)

        return model_with_owner

    def check_secret_exists(self, model_name, secret_name) -> bool:
        """Check if secret exists.

        :return: True if secret exists in the model, False otherwise
        """
        try:
            self.jhelper.get_secret_by_name(model_name, secret_name)
            return True
        except JujuSecretNotFound:
            return False


class JujuActionHelper:
    @staticmethod
    def get_unit(
        client: Client, jhelper: JujuHelper, model: str, node: str, app: str
    ) -> "str":
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

        return jhelper.get_unit_from_machine(app, machine_id, model)

    @staticmethod
    def run_action(
        client: Client,
        jhelper: JujuHelper,
        model: str,
        node: str,
        app: str,
        action_name: str,
        action_params: dict[str, typing.Any],
    ) -> dict:
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
            dict: The result of the action.

        Raises:
            UnitNotFoundException: If the unit cannot be found.
            ActionFailedException: If the action execution fails.
        """
        try:
            unit = JujuActionHelper.get_unit(client, jhelper, model, node, app)
            LOG.debug(
                "Running action '%s' on unit '%s', params: %s",
                action_name,
                unit,
                action_params,
            )

            action_result = jhelper.run_action(
                unit,
                model,
                action_name,
                action_params=action_params,
            )
            return action_result
        except UnitNotFoundException as e:
            LOG.debug(f"Application {app} not found on node {node}")
            raise e
        except ActionFailedException as e:
            LOG.debug("Action '%s' failed on node '%s': %s", action_name, node, e)
            raise e
