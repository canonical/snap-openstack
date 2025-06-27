# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Vault feature.

Vault secure, store and tightly control access to tokens, passwords, certificates,
encryption keys for protecting secrets and other sensitive data.
"""

import json
import logging

import click
import yaml
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from rich.table import Table

from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    FORMAT_DEFAULT,
    FORMAT_JSON,
    FORMAT_TABLE,
    FORMAT_YAML,
    BaseStep,
    Result,
    ResultType,
    SunbeamException,
    get_step_message,
    get_step_result,
    read_config,
    run_plan,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import get_stdin_reopen_tty
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.base import ConfigType
from sunbeam.features.interface.v1.openstack import (
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import VAULT_CHANNEL

VAULT_COMMAND_TIMEOUT = 300  # 5 minutes
VAULT_CHARM_UPDATES_TIMEOUT = (
    360  # 6 minutes, slightly greater than update-status interval
)
VAULT_APPLICATION_NAME = "vault"
VAULT_CONTAINER_NAME = "vault"
VAULT_SECRET_FOR_AUTHORIZATION = "vault-tmp-token"
VAULT_DEV_MODE_KEY = "VaultDevModeInfo"
DEFAULT_VAULT_KEY_SHARES = 1
DEFAULT_VAULT_KEY_THRESHOLD = 1

LOG = logging.getLogger(__name__)
console = Console()


class VaultCommandFailedException(SunbeamException):
    """Raised when Vault command failed."""


class VaultHelper:
    def __init__(self, jhelper: JujuHelper):
        self.jhelper = jhelper

    def _run_command_on_container(
        self, unit: str, cmd: list, env: dict[str, str] | None = None
    ):
        cmd_str = " ".join(cmd)
        res = self.jhelper.run_cmd_on_unit_payload(
            unit,
            OPENSTACK_MODEL,
            cmd_str,
            VAULT_CONTAINER_NAME,
            env,
            VAULT_COMMAND_TIMEOUT,
        )
        return res

    def get_vault_status(self, unit: str) -> dict:
        """Return Vault status.

        Raises LeaderNotFoundException if leader does not exist.
        Raises TimeoutError or JujuException if failed to run
        command on juju unit.
        """
        cmd = [
            "vault",
            "status",
            "-address=https://127.0.0.1:8200",
            "-tls-skip-verify",
            "-format=json",
        ]
        LOG.debug(f"Running vault command: {' '.join(cmd)}")
        result = self._run_command_on_container(unit, cmd)
        LOG.debug(f"Vault command result: {result}")
        return json.loads(result.get("stdout"))

    def initialize_vault(self, unit: str, key_shares: int, key_threshold: int) -> dict:
        """Initialize vault.

        Raises LeaderNotFoundException if leader does not exist.
        Raises TimeoutError or JujuException if failed to run
        command on juju unit.
        Raises VaultCommandFailedException if vault command execution failed.
        """
        cmd = [
            "vault",
            "operator",
            "init",
            f"-key-shares={key_shares}",
            f"-key-threshold={key_threshold}",
            "-address=https://127.0.0.1:8200",
            "-tls-skip-verify",
            "-format=json",
        ]
        LOG.debug(f"Running vault command: {' '.join(cmd)}")
        result = self._run_command_on_container(unit, cmd)
        # Do not log result since the result has secret keys
        LOG.debug(f"Vault command result code: {result['return-code']}")
        if result["return-code"] != 0:
            raise VaultCommandFailedException(result.get("stderr"))

        return json.loads(result.get("stdout"))

    def unseal_vault(self, unit: str, key: str) -> dict:
        """Unseal vault.

        Raises LeaderNotFoundException if leader does not exist.
        Raises TimeoutError or JujuException if failed to run
        command on juju unit.
        Raises VaultCommandFailedException if vault command execution failed.
        """
        cmd = [
            "vault",
            "operator",
            "unseal",
            "-address=https://127.0.0.1:8200",
            "-tls-skip-verify",
            "-format=json",
            key,
        ]
        LOG.debug(f"Running vault command: {' '.join(cmd[:-1])}")
        result = self._run_command_on_container(unit, cmd)
        LOG.debug(f"Vault command result: {result}")
        if result["return-code"] != 0:
            raise VaultCommandFailedException(result.get("stderr"))

        return json.loads(result.get("stdout"))

    def create_token(self, unit: str, root_token: str) -> dict:
        """Create token.

        Raises LeaderNotFoundException if leader does not exist.
        Raises TimeoutError or JujuException if failed to run
        command on juju unit.
        Raises VaultCommandFailedException if vault command execution failed.
        """
        cmd = [
            "vault",
            "token",
            "create",
            "-ttl=5m",
            "-address=https://127.0.0.1:8200",
            "-tls-skip-verify",
            "-format=json",
        ]
        env = {"VAULT_TOKEN": root_token}
        LOG.debug(f"Running vault command: {' '.join(cmd)}")
        result = self._run_command_on_container(unit, cmd, env)
        # Do not log result since the result has root token
        LOG.debug(f"Vault command result code: {result.get('return-code')}")
        if result["return-code"] != 0:
            raise VaultCommandFailedException(result.get("stderr"))

        return json.loads(result.get("stdout"))


class VaultInitStep(BaseStep):
    """Initialize Vault step."""

    def __init__(self, jhelper: JujuHelper, key_shares: int, key_threshold: int):
        super().__init__("Initialize Vault", "Initializing Vault")
        self.jhelper = jhelper
        self.vhelper = VaultHelper(jhelper)
        self.key_shares = key_shares
        self.key_threshold = key_threshold

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.leader_unit = self.jhelper.get_leader_unit(
                VAULT_APPLICATION_NAME, OPENSTACK_MODEL
            )
            res = self.vhelper.get_vault_status(self.leader_unit)
            if res.get("initialized") is True:
                LOG.info("Vault already initialized")
                return Result(ResultType.SKIPPED, "Vault already initialized.")
        except LeaderNotFoundException as e:
            LOG.debug(f"Failed to get {VAULT_APPLICATION_NAME} leader", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except TimeoutError as e:
            LOG.debug("Timeout running vault status", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except JujuException as e:
            LOG.debug("Failed to run vault status", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Runs the step.

        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        try:
            res = self.vhelper.initialize_vault(
                self.leader_unit, self.key_shares, self.key_threshold
            )
            keys: str = json.dumps(res)
        except LeaderNotFoundException as e:
            LOG.debug(f"Failed to get {VAULT_APPLICATION_NAME} leader", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except TimeoutError as e:
            LOG.debug("Timeout running vault init", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except JujuException as e:
            LOG.debug("Failed to run vault init", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except VaultCommandFailedException as e:
            LOG.debug("Failed to run vault init", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED, keys)


class VaultUnsealStep(BaseStep):
    """Unseal Vault step.

    Vault unseal command is idempotent, in the sense the command can
    be executed successfully even when the vault is not sealed. So
    execute the vault unseal command with out any skip checks.
    """

    def __init__(self, jhelper: JujuHelper, key: str):
        super().__init__("Unseal Vault", "Unsealing Vault")
        self.jhelper = jhelper
        self.vhelper = VaultHelper(jhelper)
        self.unseal_key = key

    def _get_remaining_keys_count(self, status: dict) -> int:
        """Return number of keys required to unseal."""
        progress = status.get("progress")
        # After running vault unseal command, if progress is 0
        # means the vault is unsealed. However the status of
        # attrbute sealed maynot be changed yet to False as
        # part of unseal command output.
        if progress == 0:
            return 0

        threshold = status.get("t")
        if threshold is None:
            raise VaultCommandFailedException(
                f"No threshold value in unseal result: {status}"
            )

        return threshold - progress

    def run(self, status: Status | None = None) -> Result:
        """Runs the step.

        Run vault unseal command on leader unit if it is sealed.
        If leader unit is unsealed, run vault unseal command
        on all the non leader units.

        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        unseal_status = {}

        model = OPENSTACK_MODEL

        try:
            # Run vault unseal on leader unit
            leader_unit = self.jhelper.get_leader_unit(VAULT_APPLICATION_NAME, model)
            application = self.jhelper.get_application(VAULT_APPLICATION_NAME, model)
            non_leader_units = [
                unit for unit in application.units if unit != leader_unit
            ]

            vault_status = self.vhelper.get_vault_status(leader_unit)
            if vault_status.get("sealed"):
                LOG.debug("Vault leader is sealed, running unseal on leader unit")
                res = self.vhelper.unseal_vault(leader_unit, self.unseal_key)

                # Vault unseal updates sealed attribute to False for the
                # last threshold key as part of the command execution.
                # This is not the case with non-leader units.
                if res.get("sealed"):
                    remaining = self._get_remaining_keys_count(res)
                    message = (
                        f"Vault unseal operation status: {remaining} key shares "
                        "required to unseal"
                    )
                else:
                    if len(non_leader_units) == 0:
                        message = "Vault unseal operation status: completed"
                    else:
                        # Wait for leader vault to update non-leader vault units
                        # One way to do from charm perspective is wait for status
                        # message to be changed to `Please unseal Vault`. Note the
                        # charm updates its status on update-status hook trigger.
                        # Without this change, it will be too soon on some occassions
                        # for the next unseal command to act on non-leader units.
                        (
                            self.jhelper.wait_until_desired_status(
                                OPENSTACK_MODEL,
                                [VAULT_APPLICATION_NAME],
                                units=non_leader_units,
                                status=["active", "blocked"],
                                agent_status=["idle"],
                                workload_status_message=["Please unseal Vault"],
                                timeout=VAULT_CHARM_UPDATES_TIMEOUT,
                            )
                        )

                        message = (
                            "Vault unseal operation status: completed for leader unit."
                            "\nRerun `sunbeam vault unseal` command to unseal "
                            "non-leader units."
                        )

                return Result(ResultType.COMPLETED, message)

            # Non-leader units cannot be unsealed if leader unit is sealed.
            # Leader is unsealed, apply unseal on non-leader units.
            LOG.debug("Running vault unseal command on non-leader units")
            for unit in non_leader_units:
                LOG.debug(f"Running vault unseal command on unit {unit}")
                res = self.vhelper.unseal_vault(unit, self.unseal_key)
                unseal_status[unit] = self._get_remaining_keys_count(res)

            LOG.debug(f"Unseal status non leader units: {unseal_status}")
            # Some units are sealed if remaining is greater than 0
            if any(v > 0 for k, v in unseal_status.items()):
                message = "Vault unseal operation status: "
                for unit, remaining in unseal_status.items():
                    message += f"\n{unit} : {remaining} key shares required to unseal"
            else:
                message = "Vault unseal operation status: completed"

            return Result(ResultType.COMPLETED, message)
        except ApplicationNotFoundException as e:
            LOG.debug(f"Failed to get info on {VAULT_APPLICATION_NAME}", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except TimeoutError as e:
            LOG.debug("Timeout running vault unseal", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except JujuException as e:
            LOG.debug("Failed to run vault unseal", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except VaultCommandFailedException as e:
            LOG.debug("Failed to run vault unseal", exc_info=True)
            return Result(ResultType.FAILED, str(e))


class AuthorizeVaultCharmStep(BaseStep, JujuStepHelper):
    """Authorize Vault charm step."""

    def __init__(self, jhelper: JujuHelper, token: str):
        super().__init__("Authorize Vault charm", "Authorizing Vault charm")
        self.jhelper = jhelper
        self.vhelper = VaultHelper(jhelper)
        self.token = token
        self.tmp_secret_name = VAULT_SECRET_FOR_AUTHORIZATION

    def run(self, status: Status | None = None) -> Result:
        """Runs the step.

        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        try:
            leader_unit = self.jhelper.get_leader_unit(
                VAULT_APPLICATION_NAME, OPENSTACK_MODEL
            )
            # Create temporary token
            res = self.vhelper.create_token(leader_unit, self.token)
            client_token = res.get("auth", {}).get("client_token")

            if self.check_secret_exists(OPENSTACK_MODEL, self.tmp_secret_name):
                self.jhelper.remove_secret(OPENSTACK_MODEL, self.tmp_secret_name)

            # Add generated token as a juju secret
            secret = self.jhelper.add_secret(
                OPENSTACK_MODEL,
                self.tmp_secret_name,
                {"token": client_token},
                "Temporary token for Vault charm authorization",
            )

            # Grant secret access to the vault application
            self.jhelper.grant_secret(
                OPENSTACK_MODEL, self.tmp_secret_name, VAULT_APPLICATION_NAME
            )
            LOG.debug(f"Created secret: {secret}")

            # Run authorize-charm action on vault leader unit
            action_cmd = "authorize-charm"
            action_params = {"secret-id": secret}
            action_result = self.jhelper.run_action(
                leader_unit, OPENSTACK_MODEL, action_cmd, action_params
            )

            # Remove the juju secret
            self.jhelper.remove_secret(OPENSTACK_MODEL, self.tmp_secret_name)

            LOG.debug(f"Result from action {action_cmd}: {action_result}")

        except LeaderNotFoundException as e:
            LOG.debug(f"Failed to get {VAULT_APPLICATION_NAME} leader", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except TimeoutError as e:
            LOG.debug("Timeout running vault token create", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except VaultCommandFailedException as e:
            LOG.debug("Failed to run vault token create", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except ActionFailedException as e:
            LOG.debug(f"Running action {action_cmd} on {leader_unit} failed")
            return Result(ResultType.FAILED, str(e))
        except JujuException as e:
            LOG.debug("Failed to run vault token create", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class VaultStatusStep(BaseStep):
    """Vault Status step."""

    def __init__(self, jhelper: JujuHelper):
        super().__init__("Print Vault status", "Printing Vault status")
        self.jhelper = jhelper
        self.vhelper = VaultHelper(jhelper)

    def run(self, status: Status | None = None) -> Result:
        """Runs the step.

        :return: ResultType.COMPLETED or ResultType.FAILED
        """
        consolidated_status = {}
        try:
            application = self.jhelper.get_application(
                VAULT_APPLICATION_NAME, OPENSTACK_MODEL
            )
            for unit in application.units:
                vault_status = self.vhelper.get_vault_status(unit)
                consolidated_status[unit] = vault_status

            return Result(ResultType.COMPLETED, json.dumps(consolidated_status))
        except ApplicationNotFoundException as e:
            LOG.debug(f"Failed to get info on {VAULT_APPLICATION_NAME}", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except TimeoutError as e:
            LOG.debug("Timeout running vault unseal", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except JujuException as e:
            LOG.debug("Failed to run vault unseal", exc_info=True)
            return Result(ResultType.FAILED, str(e))


class VaultFeature(OpenStackControlPlaneFeature):
    version = Version("1.0.1")

    name = "vault"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
        """Manifest pluing part in dict format."""
        return SoftwareConfig(
            charms={"vault-k8s": CharmManifest(channel=VAULT_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attrbitues to terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "vault-k8s": {
                        "channel": "vault-channel",
                        "revision": "vault-revision",
                        "config": "vault-config",
                    }
                }
            }
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return ["vault"]

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-vault": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-vault": False}

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def run_enable_plans(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Run plans to enable feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(AddManifestStep(deployment.get_client(), self.user_manifest))
        plan.extend(
            [
                TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
                EnableOpenStackApplicationStep(
                    deployment,
                    config,
                    tfhelper,
                    jhelper,
                    self,
                    app_desired_status=["blocked", "active"],
                ),
            ]
        )

        run_plan(plan, console, show_hints)
        click.echo(f"OpenStack {self.display_name} application enabled.")

    @click.command()
    @click.option(
        "--dev-mode",
        "-d",
        is_flag=True,
        help="Enable vault in dev mode.",
        default=False,
    )
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(
        self, deployment: Deployment, show_hints: bool, dev_mode: bool = False
    ) -> None:
        """Enable Vault.

        Vault secure, store and tightly control access to tokens, passwords,
        certificates, encryption keys for protecting secrets and other sensitive data.

        --dev-mode option manages the vault keys and store them in cluster database.
        Note this option is not meant for production use.
        """
        client = deployment.get_client()
        try:
            vault_info = read_config(client, VAULT_DEV_MODE_KEY)
        except ConfigItemNotFoundException:
            vault_info = {}
        except ClusterServiceUnavailableException as e:
            raise click.ClickException(
                f"Error in getting Vault information from cluster db: {str(e)}"
            )

        dev_mode_from_db = vault_info.get("dev_mode")
        if dev_mode_from_db is not None:
            if dev_mode != dev_mode_from_db:
                raise click.ClickException(
                    "--dev-mode option cannot be changed, disable and enable "
                    "vault again."
                )

        self.enable_feature(deployment, FeatureConfig(), show_hints)

        jhelper = JujuHelper(deployment.juju_controller)
        if dev_mode:
            client = deployment.get_client()

            plan1: list[BaseStep] = []
            plan2: list[BaseStep] = []
            plan3: list[BaseStep] = []

            # Skip vault init plan if unseal keys are already added in cluster db
            if not vault_info.get("unseal_keys"):
                plan1 = [
                    VaultInitStep(
                        jhelper, DEFAULT_VAULT_KEY_SHARES, DEFAULT_VAULT_KEY_THRESHOLD
                    )
                ]
                plan1_results = run_plan(plan1, console)
                result_str = get_step_message(plan1_results, VaultInitStep)
                result = json.loads(result_str)
                vault_info["dev_mode"] = True
                vault_info["unseal_keys"] = result.get("unseal_keys_b64")
                vault_info["root_token"] = result.get("root_token")
                update_config(client, VAULT_DEV_MODE_KEY, vault_info)

            # Run the loop twice, once for leader and non-leader units
            for i in range(2):
                for unseal_key in vault_info["unseal_keys"]:
                    plan2.append(VaultUnsealStep(jhelper, unseal_key))

            run_plan(plan2, console)

            root_token = vault_info.get("root_token")
            if not root_token:
                click.ClickException(
                    "Missing root token. Disable and enable vault again."
                )
                exit(1)

            plan3 = [AuthorizeVaultCharmStep(jhelper, root_token)]
            run_plan(plan3, console)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Vault."""
        self.disable_feature(deployment, show_hints)
        client = deployment.get_client()
        update_config(client, VAULT_DEV_MODE_KEY, {})

    @click.command()
    @click.argument("key-shares", type=int)
    @click.argument("key-threshold", type=int)
    @click.option(
        "-f",
        "--format",
        type=click.Choice([FORMAT_DEFAULT, FORMAT_YAML, FORMAT_JSON]),
        default=FORMAT_DEFAULT,
        help="Output format.",
    )
    @pass_method_obj
    def initialize_vault(
        self,
        deployment: Deployment,
        key_threshold: int,
        key_shares: int,
        format: str,
    ):
        """Initialize Vault."""
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [VaultInitStep(jhelper, key_shares, key_threshold)]
        plan_results = run_plan(plan, console)

        step_result = get_step_result(plan_results, VaultInitStep)
        result_str = step_result.message

        if step_result.result_type == ResultType.SKIPPED:
            if format == FORMAT_DEFAULT:
                click.echo(result_str)
            elif format == FORMAT_YAML:
                console.print(yaml.dump({}), end="")
            elif format == FORMAT_JSON:
                console.print(json.dumps({}), end="")
        elif step_result.result_type == ResultType.COMPLETED:
            result = json.loads(result_str)
            if format == FORMAT_DEFAULT:
                click.echo("Unseal keys:")
                for key in result.get("unseal_keys_b64", []):
                    click.echo(key)

                click.echo("")
                click.echo(f"Root token: {result.get('root_token')}")
            elif format == FORMAT_YAML:
                console.print(yaml.dump(result), end="")
            elif format == FORMAT_JSON:
                console.print(json.dumps(result, indent=2), end="")

    @click.command()
    @click.argument("unseal-key", type=str)
    @pass_method_obj
    def unseal_vault(self, deployment: Deployment, unseal_key: str):
        """Unseal Vault.

        Use `-` as unseal key to read from stdin.
        """
        if unseal_key == "-":
            unseal_key = get_stdin_reopen_tty()

        jhelper = JujuHelper(deployment.juju_controller)
        plan = [VaultUnsealStep(jhelper, unseal_key)]
        plan_results = run_plan(plan, console)
        click.echo(get_step_message(plan_results, VaultUnsealStep))

    @click.command()
    @click.argument("root-token", type=str)
    @pass_method_obj
    def authorize_charm(self, deployment: Deployment, root_token: str):
        """Authorize Vault charm.

        Use "-" as root_token to read from stdin.
        """
        if root_token == "-":
            root_token = get_stdin_reopen_tty()

        jhelper = JujuHelper(deployment.juju_controller)
        plan = [AuthorizeVaultCharmStep(jhelper, root_token)]
        run_plan(plan, console)
        click.echo("Vault charm is authorized.")

    @click.command()
    @click.option(
        "-f",
        "--format",
        type=click.Choice([FORMAT_TABLE, FORMAT_YAML, FORMAT_JSON]),
        default=FORMAT_TABLE,
        help="Output format.",
    )
    @pass_method_obj
    def vault_status(self, deployment: Deployment, format: str):
        """Vault status."""
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [VaultStatusStep(jhelper)]
        plan_results = run_plan(plan, console)

        result = get_step_message(plan_results, VaultStatusStep)
        status = json.loads(result)
        if format == FORMAT_TABLE:
            table = Table(title="Vault Status")
            table.add_column("Unit")
            table.add_column("Initialized")
            table.add_column("Sealed")
            for unit, status_ in status.items():
                table.add_row(
                    unit, str(status_.get("initialized")), str(status_.get("sealed"))
                )
            console.print(table)
        elif format == FORMAT_YAML:
            console.print(yaml.dump(status), end="")
        elif format == FORMAT_JSON:
            console.print(json.dumps(status, indent=2), end="")

    @click.group()
    def vault_group(self):
        """Manage Vault."""

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        LOG.debug("Vault enabled commands called")
        return {
            "init": [{"name": "vault", "command": self.vault_group}],
            "init.vault": [
                {"name": "init", "command": self.initialize_vault},
                {"name": "unseal", "command": self.unseal_vault},
                {"name": "authorize-charm", "command": self.authorize_charm},
                {"name": "status", "command": self.vault_status},
            ],
        }
