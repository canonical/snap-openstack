# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from sunbeam.core.common import BaseStep, Result, ResultType, Status
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.versions import JUJU_BASE

LOG = logging.getLogger(__name__)
APPLICATION = "tls-operator"
CHARM = "self-signed-certificates"
CERTIFICATES_APP_TIMEOUT = 1200


class DeployCertificatesProviderApplicationStep(BaseStep):
    """Deploy tls operator application."""

    def __init__(
        self,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            "Deploy tls operator",
            "Deploying TLS Operator",
        )
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.app = APPLICATION

    def is_skip(self, status: Status | None = None) -> Result:
        """Check whether or not to deploy tls operator."""
        try:
            self.jhelper.get_application(self.app, self.model)
        except ApplicationNotFoundException:
            return Result(ResultType.COMPLETED)
        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Deploy sunbeam clusterd to infra machines."""
        self.update_status(status, "fetching infra machines")
        clusterd_machines = self.jhelper.get_machines(self.model)
        machines = list(clusterd_machines.keys())

        if len(machines) == 0:
            return Result(ResultType.FAILED, f"No machines found in {self.model} model")

        # Deploy on first controller machine
        machines = machines[:1]
        self.update_status(status, "deploying application")
        charm_manifest: CharmManifest = self.manifest.core.software.charms[CHARM]
        self.jhelper.deploy(
            APPLICATION,
            CHARM,
            self.model,
            1,
            channel=charm_manifest.channel,
            revision=charm_manifest.revision,
            to=machines,
            config=charm_manifest.config,
            base=JUJU_BASE,
        )

        apps = self.jhelper.get_application_names(self.model)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=CERTIFICATES_APP_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
