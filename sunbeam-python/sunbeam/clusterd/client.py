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


from urllib.parse import quote

import requests
import requests_unixsocket
from snaphelpers import Snap

from sunbeam.clusterd.cluster import ClusterService


class Client:
    """A client for interacting with the remote client API."""

    def __init__(self, endpoint: str):
        super(Client, self).__init__()
        self._endpoint = endpoint
        self._session = requests.sessions.Session()
        if self._endpoint.startswith("http+unix://"):
            self._session.mount(
                requests_unixsocket.DEFAULT_SCHEME, requests_unixsocket.UnixAdapter()
            )
        else:
            # TODO(gboutry): remove this when proper TLS communication is
            # implemented
            self._session.verify = False

        self.cluster = ClusterService(self._session, self._endpoint)

    @classmethod
    def from_socket(cls) -> "Client":
        """Return a client initialized to the clusterd socket."""
        escaped_socket_path = quote(
            str(Snap().paths.common / "state" / "control.socket"), safe=""
        )
        return cls("http+unix://" + escaped_socket_path)

    @classmethod
    def from_http(cls, endpoint: str) -> "Client":
        """Return a client initiliazed to the clusterd http endpoint."""
        return cls(endpoint)
