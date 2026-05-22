# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path

from sunbeam.core.common import (
    BaseStep,
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

LOG = logging.getLogger(__name__)
THEME_CONFIG_SECTION = "Horizon"


class AttachHorizonThemeStep(BaseStep):
    """Prompt for and configure custom theme resources for Horizon."""

    def __init__(
        self,
        client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__("Configure Horizon Themes", "Configuring themes for Horizon")
        self.client = client
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.manifest = manifest
        self.model = model
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
                        "Enables custom themeing as well as controls built-in themes"
                    ),
                )
            },
            console=console,
            previous_answers=self.variables,
            show_hint=show_hint,
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

            self.jhelper.attach_resource(
                application="horizon",
                model=self.model,
                resource="custom-theme",
                filepath=str(theme_path),
            )

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
            horizon_config = {
                "include-default-themes": True,
                "include-ubuntu-theme": True,
                "default-theme": "ubuntu",
                "custom-theme-name": None,
            }

        override_tfvars = {"horizon-config": horizon_config}
        self.tfhelper.update_tfvars_and_apply_tf(
            self.client,
            self.manifest,
            tfvar_config=OPENSTACK_TFVAR_CONFIG_KEY,
            override_tfvars=override_tfvars,
            reporter=context.reporter,
        )
        return Result(ResultType.COMPLETED)
