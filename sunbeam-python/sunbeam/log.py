# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from rich.logging import RichHandler

MAX_LOG_FILES = 10


def setup_root_logging(logfile: Optional[Path] = None):
    """Sets up the root logging level for the application.

    By default, console logging will be turned off and level logging
    will be turned to INFO level of trace.

    The logging is configured based upon execution context, such that
    if the user's command execution is requesting quieter or more verbose
    output the logging levels will adjust.

    This will also set up the file logging in order to get execution logs
    from machines, as well as configuring the console output logging levels.
    """
    logger = logging.getLogger()
    # By default, we'll enable all debug logging.
    logger.setLevel(logging.DEBUG)
    console = False

    # NOTE(wolsen) there must be a better way to do this. In theory, we can
    #  add this to the root command group and adopt the commands everywhere
    #  and analyze the context... but it was always parsed too late.
    for arg in sys.argv:
        if arg.lower() in ["-v", "--verbose"]:
            console = True
            break

    # Some logging from the Juju (and dependent) libraries are a bit
    # noisy. Let's reduce the logging output from these dependencies.
    # TODO(wolsen) determine if we need to support a -vvv type option
    for namespace in ["websockets", "kubernetes.client"]:
        logging.getLogger(namespace).setLevel(logging.WARNING)
    # Mute juju logging to avoid missing facade warning messages
    for namespace in ["juju"]:
        logging.getLogger(namespace).setLevel(logging.ERROR)

    # If the console is enabled, then enable the RichHandler as it will
    # put the log messages to the line and still honor current console
    # entries relevant to the user.
    if console:
        handler = RichHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)

    if logfile:
        handler = logging.FileHandler(logfile)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.debug(f"Logging to {str(logfile)!r}")


def setup_logging(logfile: Union[Path, str]) -> None:
    """Sets up the logging for the specified logfile.

    :param logfile: the file to record logging information to
    :type logfile: Path or str
    :return: None
    """
    # TODO(wolsen) Use a rotating log handler?
    logging.basicConfig(
        filename=str(logfile),
        filemode="a",
        format="%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG,
    )


def prepare_logfile(path: Path, name: str) -> Path:
    """Remove older log files and return a logfile name for current execution.

    :param path: Path to the logs directoy
    :param name: name of the logfile
    """
    path.mkdir(mode=0o750, exist_ok=True)
    limit = MAX_LOG_FILES - 1
    present_files = list(path.glob(f"{name}-*.log"))
    if len(present_files) > limit:
        for fpath in sorted(present_files)[:-limit]:
            fpath.unlink(missing_ok=True)

    logfile = path / f"{name}-{datetime.now():%Y%m%d-%H%M%S.%f}.log"
    return logfile
