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

import abc

import click
from rich.console import Console

from sunbeam.clusterd.client import Client

console = Console()


class ProviderBase(abc.ABC):
    @abc.abstractmethod
    def register_add_cli(
        self,
        add: click.Group,
    ) -> None:
        """Register common commands to CLI.

        Always call to register commands that must be present.
        """
        pass

    @abc.abstractmethod
    def register_cli(
        self,
        init: click.Group,
        deployment: click.Group,
    ) -> None:
        """Register provider specific commands to CLI.

        Only called when the provider is enabled.
        """
        pass

    @abc.abstractmethod
    def get_clusterd_client(self) -> Client:
        """Get clusterd client for provider."""
        pass
