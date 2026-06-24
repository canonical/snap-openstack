# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import tarfile
import tempfile
from pathlib import Path

from sunbeam.core.common import (
    BaseStep,
    PromptMode,
    Result,
    ResultType,
    StepContext,
)
from sunbeam.core.juju import JujuException, JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.questions import (
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)

LOG = logging.getLogger(__name__)
THEME_CONFIG_SECTION = "Horizon"


def _validate_theme_path(path_str: str):
    """Validate path is a .tar.gz archive, or empty to disable theming."""
    if not path_str:
        return

    p = Path(path_str)
    if not p.is_file():
        raise ValueError(f"Theme file does not exist: {path_str}")
    if not tarfile.is_tarfile(p):
        raise ValueError(f"Theme file is not a valid tarball: {path_str}")


class AttachHorizonThemeStep(BaseStep):
    """Prompt for and attach a custom theme resource to Horizon."""

    def __init__(
        self,
        client,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        accept_defaults: bool = False,
        prompt_mode: PromptMode = PromptMode.AUTO,
        ignore_manifest_theme: bool = False,
    ):
        super().__init__("Configure Horizon Theme", "Configuring theme for Horizon")
        self.client = client
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.accept_defaults = accept_defaults
        self.prompt_mode = prompt_mode
        self.ignore_manifest_theme = ignore_manifest_theme
        self.variables: dict = {}

    def _get_horizon_config_from_manifest(self) -> dict:
        if not self.manifest or not self.manifest.core.config.horizon:
            return {}
        base = self.manifest.core.config.horizon
        if base.resources and base.resources.custom_theme:
            return {"theme_path": str(base.resources.custom_theme)}
        return {}

    def _warn_if_manifest_overrides_theme(self, console) -> None:
        manifest_cfg = self._get_horizon_config_from_manifest()
        if manifest_cfg:
            console.print(
                "[yellow]Warning:[/] your manifest defines a custom theme; "
                "this change will be reverted on the next cluster refresh."
            )

    def has_prompts(self) -> bool:
        """Indicate that this step requires interactive user input."""
        if self.prompt_mode == PromptMode.NEVER:
            return False
        if self.prompt_mode == PromptMode.FORCE:
            return True

        manifest_cfg = self._get_horizon_config_from_manifest()
        if "theme_path" in manifest_cfg:
            return False
        return True

    def prompt(self, console=None, show_hint=False) -> None:
        """Execute the interactive prompts dynamically."""
        self.variables = load_answers(self.client, THEME_CONFIG_SECTION)
        manifest_cfg = self._get_horizon_config_from_manifest()
        self.variables.update(manifest_cfg)

        bank = QuestionBank(
            questions={
                "theme_path": PromptQuestion(
                    "Custom theme archive path",
                    default_value="",
                    description=(
                        "Local filepath to a tarball (.tar.gz) created above the "
                        "root of your theme. f.e. "
                        "`tar -czf theme.tar.gz /path/to/theme`. "
                        "Leave blank to use the default theme."
                    ),
                    validation_function=_validate_theme_path,
                ),
            },
            console=console,
            previous_answers=self.variables,
            show_hint=show_hint,
            accept_defaults=self.accept_defaults,
        )
        self.variables["theme_path"] = bank.theme_path.ask()

        write_answers(self.client, THEME_CONFIG_SECTION, self.variables)

    def run(self, context: StepContext) -> Result:
        """Attach the custom theme resource to Horizon."""
        self.update_status(context, "Validating custom theme path")
        if not self.variables:
            stored = load_answers(self.client, THEME_CONFIG_SECTION)
            manifest_cfg = self._get_horizon_config_from_manifest()

            if self.ignore_manifest_theme:
                self.variables = stored
            else:
                self.variables = {**stored, **manifest_cfg}

        theme_path = self.variables.get("theme_path", "")

        if not theme_path:
            fd, theme_path = tempfile.mkstemp(suffix=".tar.gz")
            os.close(fd)

        if not Path(theme_path).is_file():
            LOG.warning("Horizon theme file is invalid or missing")
            return Result(
                ResultType.FAILED,
                f"Theme file {theme_path} is invalid or missing.",
            )

        self.update_status(context, "Attaching file resource to horizon")
        try:
            self.jhelper.attach_resource(
                "horizon",
                self.model,
                "custom-theme",
                str(theme_path),
            )
        except JujuException as e:
            LOG.exception("Failed to attach horizon theme resource")
            return Result(
                ResultType.FAILED,
                f"Failed to attach resource {theme_path}: {str(e)}",
            )

        self.update_status(context, "Waiting for Horizon to settle")
        try:
            self.jhelper.wait_application_ready(
                name="horizon",
                model=self.model,
                accepted_status=["active"],
                timeout=300,
            )
        except (JujuException, TimeoutError) as e:
            LOG.exception("Horizon unit did not settle after resource upload")
            return Result(
                ResultType.FAILED,
                f"Horizon did not settle after theme change: {str(e)}",
            )

        return Result(ResultType.COMPLETED)
