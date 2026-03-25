# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import shutil

from sunbeam.core.common import BaseStep, Result, ResultType, StepContext
from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)


class CleanTerraformPlansStep(BaseStep):
    def __init__(self, deployment: Deployment):
        super().__init__(
            "Clean terraform directories", "Cleaning terraform directories"
        )
        self.tf_plans = deployment.plans_directory

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.tf_plans.exists():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Delete terraform plan directories."""
        try:
            shutil.rmtree(self.tf_plans)
        except Exception as e:
            LOG.error("Error cleaning terraform directories: %s", e)
            return Result(ResultType.FAILED)
        return Result(ResultType.COMPLETED)
