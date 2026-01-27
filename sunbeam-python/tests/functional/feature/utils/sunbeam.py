# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sunbeam CLI wrapper for feature functional tests."""

import logging
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class SunbeamClient:
    """Client for interacting with Sunbeam CLI."""

    def __init__(self, deployment_name: str):
        self.deployment_name = deployment_name
        self._sunbeam_cmd = "/snap/bin/sunbeam"

    def _run_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """Run a sunbeam command and return the result."""
        full_command = [self._sunbeam_cmd] + command
        logger.debug("Running: %s", " ".join(full_command))

        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )

        if result.returncode != 0:
            logger.error(
                "Command failed with exit code %d: %s",
                result.returncode,
                " ".join(full_command),
            )
            if result.stderr:
                logger.error("stderr: %s", result.stderr)
            if result.stdout:
                logger.error("stdout: %s", result.stdout)
            result.check_returncode()

        return result

    def run(self, command: List[str]) -> subprocess.CompletedProcess:
        """Public helper to run arbitrary sunbeam subcommands."""
        return self._run_command(command)

    def is_connected(self) -> bool:
        """Check if we can connect to the Sunbeam deployment."""
        result = subprocess.run(
            ["sunbeam", "deployment", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and self.deployment_name in result.stdout

    def enable_feature(
        self,
        feature_name: str,
        extra_args: Optional[List[str]] = None,
    ) -> bool:
        """Enable a Sunbeam feature."""
        cmd: List[str] = ["enable", feature_name]
        if extra_args:
            cmd.extend(extra_args)

        self._run_command(cmd)
        logger.info("Feature '%s' enabled successfully", feature_name)
        return True

    def disable_feature(
        self,
        feature_name: str,
        extra_args: Optional[List[str]] = None,
    ) -> bool:
        """Disable a Sunbeam feature."""
        cmd: List[str] = ["disable", feature_name]
        if extra_args:
            cmd.extend(extra_args)

        self._run_command(cmd)
        logger.info("Feature '%s' disabled successfully", feature_name)
        return True
