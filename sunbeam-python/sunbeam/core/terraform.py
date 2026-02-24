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
            LOG.debug(
                f"Running command {' '.join(cmd)}, cwd: {self.path}, tf log: {tf_log}"
            )
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
        """Updates tfvars for specific charms and apply the plan.

        Uses source tracking to preserve computed values while updating
        only the tfvars related to specified charms from manifest.
        """
        # Step 1: Load and filter DB values (only refresh charm-specific values)
        computed_keys, updated_tfvars = self._load_and_filter_db_tfvars_for_charms(
            client, tfvar_config, charms
        )

        # Step 2: Apply manifest values for specified charms
        tfvars_from_manifest = self._get_tfvars(manifest, charms)
        self._apply_tfvars(updated_tfvars, tfvars_from_manifest)

        # Step 3: Save and apply
        if tfvar_config:
            data_to_save = dict(updated_tfvars)
            data_to_save["_computed_keys"] = list(computed_keys)
            update_config(client, tfvar_config, data_to_save)

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

        Merges tfvars from three sources with precedence Manifest > Override > DB:
        1. DB: Previously stored values (filtered by source tracking)
        2. Manifest: Values from deployment manifest
        3. Override: Runtime computed values (e.g., from features)

        Source tracking distinguishes computed values (from override_tfvars) from
        manifest-derivable values. Computed values persist across runs.

        :param tfvar_config: TerraformVar key name used to save tfvar in clusterdb
        :param override_tfvars: Terraform vars to override (computed/runtime)
        :param tf_apply_extra_args: Extra args to terraform apply command
        """
        # Step 1: Load and filter DB values
        computed_keys, updated_tfvars = self._load_and_filter_db_tfvars(
            client, tfvar_config
        )

        # Step 2: Apply manifest values (manifest > DB)
        tfvars_from_manifest = self._get_tfvars(manifest)
        self._apply_tfvars(updated_tfvars, tfvars_from_manifest)

        # Step 3: Apply override values and track as computed
        if override_tfvars:
            computed_keys.update(override_tfvars.keys())
            # For charm configs: merge manifest into override (manifest wins)
            # For non-charm configs: override wins (don't merge manifest)
            self._merge_manifest_into_override(override_tfvars, tfvars_from_manifest)
            # Apply override to final result
            self._apply_tfvars(updated_tfvars, override_tfvars)

        # Step 4: Save and apply
        if tfvar_config:
            data_to_save = dict(updated_tfvars)
            data_to_save["_computed_keys"] = list(computed_keys)
            update_config(client, tfvar_config, data_to_save)

        self.write_tfvars(updated_tfvars)
        LOG.debug(f"Applying plan {self.plan} with tfvars {updated_tfvars}")
        self.apply(tf_apply_extra_args)

    def _load_and_filter_db_tfvars(
        self, client: Client, tfvar_config: str | None
    ) -> tuple[set, dict]:
        """Load tfvars from DB and filter based on source tracking.

        Returns tuple of (computed_keys, filtered_tfvars).
        """
        computed_keys: set = set()
        updated_tfvars: dict = {}

        if not tfvar_config:
            return computed_keys, updated_tfvars

        try:
            stored_data = read_config(client, tfvar_config)
            computed_keys = set(stored_data.get("_computed_keys", []))

            # Migration: use preserve list if no computed_keys yet
            if not computed_keys and "_computed_keys" not in stored_data:
                computed_keys = set(self.tfvar_map.get("preserve", []))

            # Filter: keep computed or non-manifest-derivable values
            current_tfvars = {
                k: v for k, v in stored_data.items() if k != "_computed_keys"
            }
            manifest_derivable = set(self._get_tfvar_names())

            for key, value in current_tfvars.items():
                if key in computed_keys or key not in manifest_derivable:
                    updated_tfvars[key] = value

        except ConfigItemNotFoundException:
            pass

        return computed_keys, updated_tfvars

    def _apply_tfvars(self, target: dict, source: dict) -> None:
        """Apply source tfvars to target with charm config merging.

        For charm configs (dicts): merge fields (source wins conflicts).
        For other values: source replaces target.
        Modifies target in place.
        """
        charm_config_keys = self._get_charm_config_keys()

        for key, source_value in source.items():
            if key in charm_config_keys:
                # Charm config: merge dicts
                if key in target:
                    target_value = target[key]
                    if isinstance(target_value, dict) and isinstance(
                        source_value, dict
                    ):
                        target_value.update(source_value)
                    else:
                        target[key] = source_value
                else:
                    target[key] = source_value
            else:
                # Non-charm config: simple replacement
                target[key] = source_value

    def _merge_manifest_into_override(
        self, override_tfvars: dict, manifest_tfvars: dict
    ) -> None:
        """Merge manifest into override for charm configs only.

        For charm configs (dicts): merge manifest fields (manifest wins).
        For non-charm configs: keep override values (don't touch them).
        This ensures manifest precedence for charm configs while preserving
        override precedence for non-charm configs.
        Modifies override_tfvars in place.
        """
        charm_config_keys = self._get_charm_config_keys()

        for key in charm_config_keys:
            if key in override_tfvars and key in manifest_tfvars:
                override_value = override_tfvars[key]
                manifest_value = manifest_tfvars[key]
                if isinstance(override_value, dict) and isinstance(
                    manifest_value, dict
                ):
                    override_value.update(manifest_value)

    def _load_and_filter_db_tfvars_for_charms(
        self, client: Client, tfvar_config: str | None, charms: list[str]
    ) -> tuple[set, dict]:
        """Load tfvars from DB and filter for partial charm updates.

        Only refreshes tfvars related to specified charms from manifest.
        Keeps all computed values and values unrelated to these charms.

        Returns tuple of (computed_keys, filtered_tfvars).
        """
        computed_keys: set = set()
        updated_tfvars: dict = {}

        if not tfvar_config:
            return computed_keys, updated_tfvars

        try:
            stored_data = read_config(client, tfvar_config)
            computed_keys = set(stored_data.get("_computed_keys", []))

            # Migration: use preserve list if no computed_keys yet
            if not computed_keys and "_computed_keys" not in stored_data:
                computed_keys = set(self.tfvar_map.get("preserve", []))

            # Filter: keep computed and non-charm-specific values
            current_tfvars = {
                k: v for k, v in stored_data.items() if k != "_computed_keys"
            }
            charm_tfvar_names = set(self._get_tfvar_names(charms))

            for key, value in current_tfvars.items():
                if key in computed_keys or key not in charm_tfvar_names:
                    updated_tfvars[key] = value

        except ConfigItemNotFoundException:
            pass

        return computed_keys, updated_tfvars

    def _get_charm_config_keys(self) -> set[str]:
        """Get the set of charm config tfvar keys.

        :return: Set of charm config keys defined in tfvar_map
        """
        config_tfvars = set()
        for _, per_charm_tfvar_map in self.tfvar_map.get("charms", {}).items():
            config_key = per_charm_tfvar_map.get("config")
            if config_key:
                config_tfvars.add(config_key)
        return config_tfvars

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
