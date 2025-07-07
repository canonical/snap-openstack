# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from string import Template

from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import BaseStep, Result, ResultType, read_config, update_config
from sunbeam.core.manifest import Manifest
from sunbeam.versions import VarMap

LOG = logging.getLogger(__name__)
TERRAFORM_APPLY_TIMEOUT = 1200  # 20 minutes

http_backend_template = """
terraform {
  backend "http" {
    address                = $address
    update_method          = $update_method
    lock_address           = $lock_address
    lock_method            = $lock_method
    unlock_address         = $unlock_address
    unlock_method          = $unlock_method
    skip_cert_verification = $skip_cert_verification
  }
}
"""

terraform_rc_template = """
disable_checkpoint = true
provider_installation {
  filesystem_mirror {
    path    = "$snap_path/usr/share/terraform-providers"
  }
}
"""


class TerraformException(Exception):
    """Terraform related exceptions."""

    def __init__(self, message):
        super().__init__()
        self.message = message

    def __str__(self) -> str:
        """Stringify the exception."""
        return self.message


class TerraformStateLockedException(Exception):
    """Terraform Remote State Locked Exception."""

    def __init__(self, message):
        super().__init__()
        self.message = message

    def __str__(self) -> str:
        """Stringify the exception."""
        return self.message


class TerraformHelper:
    """Helper for interaction with Terraform."""

    def __init__(
        self,
        path: Path,
        plan: str,
        tfvar_map: VarMap,
        env: dict | None = None,
        parallelism: int | None = None,
        backend: str | None = None,
        clusterd_address: str | None = None,
    ):
        self.snap = Snap()
        self.path = path
        self.plan = plan
        self.tfvar_map = tfvar_map
        self.env = env
        self.parallelism = parallelism
        self.backend = backend or "local"
        self.terraform = str(self.snap.paths.snap / "bin" / "terraform")
        self.clusterd_address = clusterd_address

    def backend_config(self) -> dict:
        """Get backend configuration for terraform."""
        if self.backend == "http" and self.clusterd_address is not None:
            address = self.clusterd_address
            return {
                "address": f"{address}/1.0/terraformstate/{self.plan}",
                "update_method": "PUT",
                "lock_address": f"{address}/1.0/terraformlock/{self.plan}",
                "lock_method": "PUT",
                "unlock_address": f"{address}/1.0/terraformunlock/{self.plan}",
                "unlock_method": "PUT",
                "skip_cert_verification": True,
            }
        return {}

    def write_backend_tf(self) -> bool:
        """Write backend configuration to backend.tf file.

        This is injecting clusterd as http backend for the terraform state.
        """
        backend = self.backend_config()
        if self.backend == "http":
            backend_obj = Template(http_backend_template)
            backend_templated = backend_obj.safe_substitute(
                {key: json.dumps(value) for key, value in backend.items()}
            )
            backend_path = self.path / "backend.tf"
            old_backend = None
            if backend_path.exists():
                old_backend = backend_path.read_text()
            if old_backend != backend_templated:
                with backend_path.open(mode="w") as file:
                    file.write(backend_templated)
                return True
        return False

    def write_tfvars(self, vars: dict, location: Path | None = None) -> None:
        """Write terraform variables file."""
        filepath = location or (self.path / "terraform.tfvars.json")
        with filepath.open("w") as tfvars:
            tfvars.write(json.dumps(vars))

    def write_terraformrc(self) -> None:
        """Write .terraformrc file."""
        terraform_rc = self.snap.paths.user_data / ".terraformrc"
        with terraform_rc.open(mode="w") as file:
            file.write(
                Template(terraform_rc_template).safe_substitute(
                    {"snap_path": self.snap.paths.snap}
                )
            )

    def reload_env(self, env: dict) -> None:
        """Update environment variables."""
        if self.env:
            self.env.update(env)
        else:
            self.env = env

    def init(self) -> None:
        """Terraform init."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-init-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)
        backend_updated = False
        if self.backend:
            backend_updated = self.write_backend_tf()
        self.write_terraformrc()

        try:
            cmd = [self.terraform, "init", "-upgrade", "-no-color"]
            if backend_updated:
                LOG.debug("Backend updated, running terraform init -reconfigure")
                cmd.append("-reconfigure")
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform init failed: {e.output}")
            LOG.warning(e.stderr)
            raise TerraformException(str(e))

    def apply(self, extra_args: list | None = None):
        """Terraform apply."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-apply-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "apply"]
            if extra_args:
                cmd.extend(extra_args)
            cmd.extend(["-auto-approve", "-no-color"])
            if self.parallelism is not None:
                cmd.append(f"-parallelism={self.parallelism}")
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
                timeout=TERRAFORM_APPLY_TIMEOUT,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform apply failed: {e.output}")
            LOG.warning(e.stderr)
            if "remote state already locked" in e.stderr:
                raise TerraformStateLockedException(str(e))
            else:
                raise TerraformException(str(e))

    def destroy(self):
        """Terraform destroy."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-destroy-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [
                self.terraform,
                "destroy",
                "-auto-approve",
                "-no-color",
                "-input=false",
            ]
            if self.parallelism is not None:
                cmd.append(f"-parallelism={self.parallelism}")
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform destroy failed: {e.output}")
            LOG.warning(e.stderr)
            raise TerraformException(str(e))

    def output(self, hide_output: bool = False) -> dict:
        """Terraform output."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-output-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "output", "-json", "-no-color"]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            stdout = process.stdout
            logged_output = ""
            if not hide_output:
                logged_output = f" stdout={stdout}, stderr={process.stderr}"
            LOG.debug("Command finished." + logged_output)
            tf_output = json.loads(stdout)
            output = {}
            for key, value in tf_output.items():
                output[key] = value["value"]
            return output
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform output failed: {e.output}")
            LOG.warning(e.stderr)
            raise TerraformException(str(e))

    def pull_state(self) -> dict:
        """Pull the Terraform state."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-state-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "state", "pull"]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            # don't log the state as it can be large and contain sensitive data
            LOG.debug(f"Command finished. stderr={process.stderr}")
            return json.loads(process.stdout)
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform state pull failed: {e.output}")
            LOG.error(e.stderr)
            raise TerraformException(str(e))

    def state_list(self) -> list:
        """List the Terraform state."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-state-list-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "state", "list"]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
            return process.stdout.splitlines()
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform state list failed: {e.output}")
            LOG.error(e.stderr)
            raise TerraformException(str(e))

    def state_rm(self, resource: str) -> None:
        """Remove a resource from Terraform state."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-state-rm-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "state", "rm", resource]
            LOG.debug(f"Running command {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform state rm failed: {e.output}")
            LOG.error(e.stderr)
            raise TerraformException(str(e))

    def sync(self) -> None:
        """Sync the running state back to the Terraform state file."""
        os_env = os.environ.copy()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tf_log = str(self.path / f"terraform-sync-{timestamp}.log")
        os_env.update({"TF_LOG_PATH": tf_log})
        os_env.setdefault("TF_LOG", "INFO")
        if self.env:
            os_env.update(self.env)

        try:
            cmd = [self.terraform, "apply", "-refresh-only", "-auto-approve"]
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.path,
                env=os_env,
            )
            LOG.debug(
                f"Command finished. stdout={process.stdout}, stderr={process.stderr}"
            )
        except subprocess.CalledProcessError as e:
            LOG.error(f"terraform sync failed: {e.output}")
            LOG.error(e.stderr)
            raise TerraformException(str(e))

    def update_partial_tfvars_and_apply_tf(
        self,
        client: Client,
        manifest: Manifest,
        charms: list[str],
        tfvar_config: str | None = None,
        tf_apply_extra_args: list | None = None,
    ) -> None:
        """Updates tfvars for specific charms and apply the plan."""
        current_tfvars = {}
        updated_tfvars = {}
        if tfvar_config:
            try:
                current_tfvars = read_config(client, tfvar_config)
                # Exclude all default tfvar keys from the previous terraform
                # vars applied to the plan. Ignore the keys that should
                # be preserved.
                _tfvar_names = set(self._get_tfvar_names()).difference(
                    self.tfvar_map.get("preserve", [])
                )
                updated_tfvars = {
                    k: v for k, v in current_tfvars.items() if k not in _tfvar_names
                }
            except ConfigItemNotFoundException:
                pass

        updated_tfvars.update(self._get_tfvars(manifest, charms))
        if tfvar_config:
            update_config(client, tfvar_config, updated_tfvars)

        self.write_tfvars(updated_tfvars)
        LOG.debug(f"Applying plan {self.plan} with tfvars {updated_tfvars}")
        self.apply(tf_apply_extra_args)

    def update_tfvars_and_apply_tf(
        self,
        client: Client,
        manifest: Manifest,
        tfvar_config: str | None = None,
        override_tfvars: dict | None = None,
        tf_apply_extra_args: list | None = None,
    ) -> None:
        """Updates terraform vars and Apply the terraform.

        Get tfvars from cluster db using tfvar_config key, Manifest file using
        Charm Manifest tfvar map from core and features, User provided override_tfvars.
        Merge the tfvars in the above order so that terraform vars in override_tfvars
        will have highest priority.
        Get tfhelper object for tfplan and write tfvars and apply the terraform plan.

        :param tfvar_config: TerraformVar key name used to save tfvar in clusterdb
        :type tfvar_config: str or None
        :param override_tfvars: Terraform vars to override
        :type override_tfvars: dict
        :param tf_apply_extra_args: Extra args to terraform apply command
        :type tf_apply_extra_args: list or None
        """
        updated_tfvars = {}
        if tfvar_config:
            try:
                current_tfvars = read_config(client, tfvar_config)
                # Exclude all default tfvar keys from the previous terraform
                # vars applied to the plan. Ignore the keys that should
                # be preserved.
                _tfvar_names = set(self._get_tfvar_names()).difference(
                    self.tfvar_map.get("preserve", [])
                )
                updated_tfvars = {
                    k: v for k, v in current_tfvars.items() if k not in _tfvar_names
                }
            except ConfigItemNotFoundException:
                pass

        # NOTE: It is expected for Manifest to contain all previous changes
        # So override tfvars from configdb to defaults if not specified in
        # manifest file
        tfvars_from_manifest = self._get_tfvars(manifest)
        updated_tfvars.update(tfvars_from_manifest)

        if override_tfvars:
            self._handle_charm_configs_in_override_tfvars(
                override_tfvars, tfvars_from_manifest
            )
            updated_tfvars.update(override_tfvars)

        if tfvar_config:
            update_config(client, tfvar_config, updated_tfvars)

        self.write_tfvars(updated_tfvars)
        LOG.debug(f"Applying plan {self.plan} with tfvars {updated_tfvars}")
        self.apply(tf_apply_extra_args)

    def _handle_charm_configs_in_override_tfvars(
        self, override_tfvars: dict, tfvars_from_manifest: dict
    ):
        # Fetch all tfvar names of charm configs
        config_tfvars = [
            per_charm_tfvar_map.get("config")
            for _, per_charm_tfvar_map in self.tfvar_map.get("charms", {}).items()
        ]

        # charm configs are dict, so require union of configs from overrides
        # and manifest with precedence for overrides
        for override_config in override_tfvars:
            if override_config in config_tfvars:
                override_tfvars[override_config].update(
                    tfvars_from_manifest.get(override_config, {})
                )

    def _get_tfvars(self, manifest: Manifest, charms: list | None = None) -> dict:
        """Get tfvars from the manifest.

        MANIFEST_ATTRIBUTES_TFVAR_MAP holds the mapping of Manifest attributes
        and the terraform variable name. For each terraform variable in
        MANIFEST_ATTRIBUTES_TFVAR_MAP, get the corresponding value from Manifest
        and return all terraform variables as dict.

        If charms is passed as input, filter the charms based on the list
        provided.
        """
        tfvars = {}

        charms_tfvar_map = self.tfvar_map.get("charms", {})
        if charms:
            charms_tfvar_map = {
                k: v for k, v in charms_tfvar_map.items() if k in charms
            }

        # handle tfvars for charms section
        for charm, per_charm_tfvar_map in charms_tfvar_map.items():
            charm_manifest = manifest.core.software.charms.get(charm)
            if not charm_manifest:
                for _, feature in manifest.get_features():
                    charm_manifest = feature.software.charms.get(charm)
                    if charm_manifest:
                        break
            if charm_manifest:
                manifest_charm = charm_manifest.model_dump(by_alias=True)
                for charm_attribute_name, tfvar_name in per_charm_tfvar_map.items():
                    charm_attribute_value = manifest_charm.get(charm_attribute_name)
                    if charm_attribute_value:
                        tfvars[tfvar_name] = charm_attribute_value

        return tfvars

    def _get_tfvar_names(self, charms: list | None = None) -> list[str]:
        if charms:
            return [
                tfvar_name
                for charm, per_charm_tfvar_map in self.tfvar_map.get(
                    "charms", {}
                ).items()
                for _, tfvar_name in per_charm_tfvar_map.items()
                if charm in charms
            ]
        else:
            return [
                tfvar_name
                for _, per_charm_tfvar_map in self.tfvar_map.get("charms", {}).items()
                for _, tfvar_name in per_charm_tfvar_map.items()
            ]


class TerraformInitStep(BaseStep):
    """Initialize Terraform with required providers."""

    def __init__(self, tfhelper: TerraformHelper):
        super().__init__(
            "Initialize Terraform", "Initializing Terraform from provider mirror"
        )
        self.tfhelper = tfhelper

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Initialise Terraform configuration from provider mirror,."""
        try:
            self.tfhelper.init()
            return Result(ResultType.COMPLETED)
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))
