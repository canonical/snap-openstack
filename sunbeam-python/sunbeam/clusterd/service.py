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
from abc import ABC
from urllib.parse import quote

from requests.exceptions import ConnectionError, HTTPError
from requests.sessions import Session
from requests_unixsocket import DEFAULT_SCHEME
from snaphelpers import Snap

LOG = logging.getLogger(__name__)


class RemoteException(Exception):
    """An Exception raised when interacting with the remote microclusterd service"""

    pass


class ClusterAlreadyBootstrappedException(RemoteException):
    """Raised when cluster service is already bootstrapped"""

    pass


class ClusterServiceUnavailableException(RemoteException):
    """Raised when cluster service is not yet bootstrapped"""

    pass


class ConfigItemNotFoundException(RemoteException):
    """Raise when ConfigItem cannot be found on the remote"""

    pass


class NodeAlreadyExistsException(RemoteException):
    """Raised when the node already exists"""

    pass


class NodeNotExistInClusterException(RemoteException):
    """Raised when the node does not exist in cluster"""

    pass


class NodeJoinException(RemoteException):
    """Raised when the node not able to join cluster"""

    pass


class LastNodeRemovalFromClusterException(RemoteException):
    """Raised when token is already generated for the node"""

    pass


class TokenAlreadyGeneratedException(RemoteException):
    """Raised when token is already generated for the node"""

    pass


class TokenNotFoundException(RemoteException):
    """Raised when token is not found for the node"""

    pass


class JujuUserNotFoundException(RemoteException):
    """Raise when jujuuser is not found"""

    pass


class BaseService(ABC):
    """BaseService is the base service class for sunbeam clusterd services."""

    def __init__(self, session: Session):
        """Creates a new BaseService for the sunbeam clusterd API

        The service class is used to provide convenient APIs for clients to
        use when interacting with the sunbeam clusterd api.


        :param session: session to use when interacting with the sunbeam clusterd API
        :type: Session
        """
        self.__session = session
        self._socket_path = Snap().paths.common / "state" / "control.socket"
        LOG.debug(self._socket_path)

    def _request(self, method, path, **kwargs):
        if path.startswith("/"):
            path = path[1:]
        netloc = quote(str(self._socket_path), safe="")
        url = f"{DEFAULT_SCHEME}{netloc}/{path}"

        try:
            LOG.debug("[%s] %s, args=%s", method, url, kwargs)
            response = self.__session.request(method=method, url=url, **kwargs)
            LOG.debug("Response(%s) = %s", response, response.text)
        except ConnectionError as e:
            msg = str(e)
            if "FileNotFoundError" in msg:
                raise ClusterServiceUnavailableException(
                    "Sunbeam Cluster socket not found, is clusterd running ?"
                    " Check with 'snap services openstack.clusterd'",
                ) from e
            raise ClusterServiceUnavailableException(msg)

        try:
            response.raise_for_status()
        except HTTPError as e:
            # Do some nice translating to sunbeamdexceptions
            error = response.json().get("error")
            if "remote with name" in error:
                raise NodeAlreadyExistsException(
                    "Already node exists in the sunbeam cluster"
                )
            elif "No remote exists with the given name" in error:
                raise NodeNotExistInClusterException(
                    "Node does not exist in the sunbeam cluster"
                )
            elif "Node not found" in error:
                raise NodeNotExistInClusterException(
                    "Node does not exist in the sunbeam cluster"
                )
            elif "Failed to join cluster with the given join token" in error:
                raise NodeJoinException(
                    "Join node to cluster failed with the given token"
                )
            elif "UNIQUE constraint failed: internal_token_records.name" in error:
                raise TokenAlreadyGeneratedException(
                    "Token already generated for the node"
                )
            elif "Daemon not yet initialized" in error:
                raise ClusterServiceUnavailableException(
                    "Sunbeam Cluster not initialized"
                )
            elif "InternalTokenRecord not found" in error:
                raise TokenNotFoundException("Token not found for the node")
            elif (
                "Cannot remove cluster members, there are no remaining "
                "non-pending members"
            ) in error:
                raise LastNodeRemovalFromClusterException(
                    "Cannot remove cluster member as there are no remaining "
                    "non-pending members. Reset the last node instead."
                )
            elif "already running" in error:
                raise ClusterAlreadyBootstrappedException(
                    "Already cluster is bootstrapped."
                )
            elif "ConfigItem not found" in error:
                raise ConfigItemNotFoundException("ConfigItem not found")
            raise e

        return response.json()

    def _get(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", True)
        return self._request("get", path, **kwargs)

    def _head(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", False)
        return self._request("head", path, **kwargs)

    def _post(self, path, data=None, json=None, **kwargs):
        return self._request("post", path, data=data, json=json, **kwargs)

    def _patch(self, path, data=None, **kwargs):
        return self._request("patch", path, data=data, **kwargs)

    def _put(self, path, data=None, **kwargs):
        return self._request("put", path, data=data, **kwargs)

    def _delete(self, path, **kwargs):
        return self._request("delete", path, **kwargs)

    def _options(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", True)
        return self._request("options", path, **kwargs)
