# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, StepContext

LOG = logging.getLogger(__name__)


class SetBootstrapped(BaseStep):
    """Post Deployment step to update bootstrap flag in cluster DB."""

    def __init__(self, client: Client):
        super().__init__("Mark bootstrapped", "Mark deployment bootstrapped")
        self.client = client

    def run(self, context: StepContext) -> Result:
        """Set deployment as bootstrapped in clusterd."""
        LOG.debug("Setting deployment as bootstrapped")
        self.client.cluster.set_sunbeam_bootstrapped()
        return Result(ResultType.COMPLETED)
