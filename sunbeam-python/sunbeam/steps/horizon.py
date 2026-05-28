# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import (
    BaseStep,
    read_config,
    Result,
    ResultType,
    StepContext,
)
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.questions import (
    ConfirmQuestion,
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.core.terraform import TerraformHelper
from sunbeam.steps.openstack import CONFIG_KEY as OPENSTACK_TFVAR_CONFIG_KEY
from tarfile import is_tarfile

LOG = logging.getLogger(__name__)
THEME_CONFIG_SECTION = "Horizon"

def _validate_theme_path(path_str: str):
    """Validate path is a non-empty .tar.gz archive and contains a valid theme"""
    if not path_str:
        raise ValueError("Theme path is required")
    if not path_str.endswith(".tar.gz", ".tgz"):
        raise ValueError(f"Theme file must be a .tar.gz archive: {path_str}")

    p = Path(path_str)
    if not p.is_file():
        raise ValueError(f"Theme file does not exist: {path_str}")
    if not is_tarfile(p):
        raise ValueError(f"Theme file is not a valid tarball: {path_str}")

class AttachHorizonThemeStep(BaseStep):
    """Prompt for and configure custom theme resources for Horizon."""

    def __init__(
        self,
        client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        manifest: Manifest,
        model: str,
        accept_defaults: bool = False,
    ):
        super().__init__("Configure Horizon Themes", "Configuring themes for Horizon")
        self.client = client
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.manifest = manifest
        self.model = model
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def has_prompts(self) -> bool:
        """Indicate that this step requires interactive user input."""
        return True

    def prompt(self, console=None, show_hint=False) -> None:
        """Execute the interactive prompts dynamically."""
        self.variables = load_answers(self.client, THEME_CONFIG_SECTION)

        enable_bank = QuestionBank(
            questions={
                "enable_custom_theme": ConfirmQuestion(
                    "Customize available Horizon themes?",
                    default_value=False,
                    description=(
                        "Enables custom theming as well as controls built-in themes"
                    ),
                )
            },
            console=console,
            previous_answers=self.variables,
            show_hint=show_hint,
            accept_defaults=self.accept_defaults,
        )
        enable = enable_bank.enable_custom_theme.ask()
        self.variables["enable_custom_theme"] = enable

        if enable:
            details_bank = QuestionBank(
                questions={
                    "custom_theme_name": PromptQuestion(
                        "Custom theme name",
                        default_value="custom",
                        description=(
                            "Name that will be used for the theme folder "
                            "as well as displayed in GUI"
                        ),
                    ),
                    "theme_path": PromptQuestion(
                        "Custom theme archive path",
                        default_value="",
                        description=(
                            "Local filepath to a tarball (.tar.gz) created"
                            "at the root of your theme"
                        ),
                        validation_function=_validate_theme_path,
                    ),
                    "disable_default_themes": ConfirmQuestion(
                        "Disable default openstack themes",
                        default_value=False,
                        description=(
                            "Disables default and material themes "
                            "included by upstream OpenStack"
                        ),
                    ),
                    "disable_ubuntu_theme": ConfirmQuestion(
                        "Disable included ubuntu theme",
                        default_value=False,
                        description=(
                            "Disables included Ubuntu theme (the Sunbeam default theme)"
                        ),
                    ),
                },
                console=console,
                previous_answers=self.variables,
                show_hint=show_hint,
                accept_defaults=self.accept_defaults,
            )
            self.variables["custom_theme_name"] = details_bank.custom_theme_name.ask()
            self.variables["theme_path"] = details_bank.theme_path.ask()
            self.variables["disable_default_themes"] = (
                details_bank.disable_default_themes.ask()
            )
            self.variables["disable_ubuntu_theme"] = (
                details_bank.disable_ubuntu_theme.ask()
            )

            if not self.variables["disable_ubuntu_theme"]:
                def_theme = "ubuntu"
            elif not self.variables["disable_default_themes"]:
                def_theme = "default"
            else:
                def_theme = self.variables["custom_theme_name"]

            default_theme_bank = QuestionBank(
                questions={
                    "default_theme": PromptQuestion(
                        "Default theme",
                        default_value=def_theme,
                        description="Theme to be selected by default in the UI",
                    )
                },
                console=console,
                previous_answers=self.variables,
                show_hint=show_hint,
                accept_defaults=self.accept_defaults,
            )
            self.variables["default_theme"] = default_theme_bank.default_theme.ask()

        write_answers(self.client, THEME_CONFIG_SECTION, self.variables)

    def run(self, context: StepContext) -> Result:
        """Attach the resource and push the configuration via Terraform."""

        self.variables = load_answers(self.client, THEME_CONFIG_SECTION)
        if self.variables.get("enable_custom_theme"):
            theme_path = Path(self.variables.get("theme_path", ""))
            if not theme_path.exists() or not theme_path.is_file():
                return Result(
                    ResultType.FAILED, f"Theme file {theme_path} is invalid or missing."
                )
            try:
                theme_revision = self.jhelper.attach_resource(
                    application="horizon",
                    model=self.model,
                    resource="custom-theme",
                    filepath=str(theme_path),
                )
            except JujuException as e:
                LOG.expection("Failed to attach horizon theme resource")
                return Result(ResultType.FAILED, f"failed to attach resource: {str(theme_path)}")

            horizon_resources = {"custom-theme": theme_revision}

            horizon_config = {
                "include-default-themes": not self.variables.get(
                    "disable_default_themes", False
                ),
                "include-ubuntu-theme": not self.variables.get(
                    "disable_ubuntu_theme", False
                ),
                "default-theme": self.variables.get("default_theme", "ubuntu"),
                "custom-theme-name": self.variables.get("custom_theme_name", "custom"),
            }
        else:
            horizon_resources = {}

            horizon_config = {
                "include-default-themes": True,
                "include-ubuntu-theme": True,
                "default-theme": "ubuntu",
                "custom-theme-name": None,
            }

        try:
            current_tfvars = read_config(self.client, OPENSTACK_TFVAR_CONFIG_KEY)
        except ConfigItemNotFoundException:
            current_tfvars = {}
        except Exception as e:
            LOG.exception("Error reading current tfvars")
            return Result(ResultType.FAILED, f"failed to read tfvars: {str(e)}")

        merged_resources = {**current_tfvars.get("horizon-resources", {}), **horizon_resources}
        merged_config = {**current_tfvars.get("horizon-config", {}), **horizon_config}

        override_tfvars = {
            "horizon-resources": merged_resources,
            "horizon-config": merged_config,
        }

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=OPENSTACK_TFVAR_CONFIG_KEY,
                override_tfvars=override_tfvars,
                reporter=context.reporter,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.exception("Failed to update tfvars")
            return Result(ResultType.FAILED, f"failed to update tfvars: {str(override_tfvars)}")
        return Result(ResultType.COMPLETED)
