# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

from rich.console import Console

from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    SunbeamException,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ModelNotFoundException,
)
from sunbeam.features.interface.v1.base import (
    EnableDisableFeature,
    HasRequirersFeaturesError,
)

LOG = logging.getLogger(__name__)


class DisableEnabledFeatures(BaseStep):
    """Destroy a set of features in a Juju model."""

    def __init__(self, deployment: Deployment, no_prompt: bool = False):
        super().__init__("Disable features", "Disabling features")
        self.deployment = deployment
        self.no_prompt = no_prompt
        self.feature_manager = deployment.get_feature_manager()
        self.enabled_features: queue.Queue["EnableDisableFeature"] | None

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return not self.no_prompt

    def prompt(self, console: Console | None = None, show_hint: bool = False) -> None:
        """Determines if the step can take input from the user."""
        return super().prompt(console, show_hint)

    def is_skip(self, context: StepContext) -> Result:
        """Check if the step should be skipped."""
        try:
            enabled_features = self.feature_manager.enabled_features(self.deployment)
        except ClusterServiceUnavailableException:
            return Result(ResultType.SKIPPED)
        if not enabled_features:
            return Result(ResultType.SKIPPED)
        self.enabled_features = queue.Queue()
        for feature in enabled_features:
            self.enabled_features.put(feature)
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Disable the features."""
        if not self.enabled_features:
            return Result(ResultType.COMPLETED)

        while not self.enabled_features.empty():
            feature = self.enabled_features.get()
            LOG.info("Disabling feature %s", feature.name)
            try:
                feature.disable_feature(self.deployment, False)
            except HasRequirersFeaturesError:
                LOG.info("Feature %s has requirers, retrying later", feature.name)
                self.enabled_features.put(feature)
                continue
            except ModelNotFoundException as e:
                return Result(
                    ResultType.FAILED,
                    f"Model not found: {e}",
                )
            except SunbeamException as e:
                return Result(
                    ResultType.FAILED,
                    f"Failed to disable feature {feature.name}: {e}",
                )
        return Result(ResultType.COMPLETED, "Features disabled")
