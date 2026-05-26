# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""GPFS backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class ImagesShareMode(StrEnum):
    """Enumeration of valid image share modes."""

    COPY = "copy"
    COPY_ON_WRITE = "copy_on_write"


class IbmgpfsConfig(StorageBackendConfig):
    """Configuration model for GPFS backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Optional backend configuration
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    gpfs_mount_point_base: Annotated[
        str | None,
        Field(
            description="Specifies the path of the GPFS directory where Block Storage volume and snapshot files are stored."  # noqa: E501
        ),
    ] = None
    gpfs_images_dir: Annotated[
        str | None,
        Field(
            description="Specifies the path of the Image service repository in GPFS. Leave undefined if not storing images in GPFS."  # noqa: E501
        ),
    ] = None
    gpfs_images_share_mode: Annotated[
        ImagesShareMode | None,
        Field(description="Specifies the type of image copy to be used."),
    ] = None
    gpfs_max_clone_depth: Annotated[
        int | None,
        Field(
            description="Specifies an upper limit on the number of indirections required to reach a specific block due to snapshots or clones."  # noqa: E501
        ),
    ] = None
    gpfs_sparse_volumes: Annotated[
        bool | None,
        Field(
            description="Specifies that volumes are created as sparse files which initially consume no space."  # noqa: E501
        ),
    ] = None
    gpfs_storage_pool: Annotated[
        str | None,
        Field(description="Specifies the storage pool that volumes are assigned to."),
    ] = None
    gpfs_hosts: Annotated[
        str | None,
        Field(
            description="Comma-separated list of IP address or hostnames of GPFS nodes."
        ),
    ] = None
    gpfs_user_login: Annotated[str, Field(description="Username for GPFS nodes.")]
    gpfs_user_password: Annotated[
        str,
        Field(description="Password for GPFS node user."),
        SecretDictField(field="gpfs-user-password"),
    ]
    gpfs_private_key: Annotated[
        str | None,
        Field(description="Filename of private key to use for SSH authentication."),
    ] = None
    gpfs_ssh_port: Annotated[int | None, Field(description="SSH port to use.")] = None
    gpfs_hosts_key_file: Annotated[
        str | None,
        Field(
            description="File containing SSH host keys for the gpfs nodes with which driver needs to communicate."  # noqa: E501
        ),
    ] = None
    gpfs_strict_host_key_policy: Annotated[
        bool | None,
        Field(
            description="Option to enable strict gpfs host key checking while connecting to gpfs nodes."  # noqa: E501
        ),
    ] = None
    san_thin_provision: Annotated[
        bool | None, Field(description="Use thin provisioning for SAN volumes?")
    ] = None
    san_ip: Annotated[str, Field(description="IP address of SAN controller")]
    san_login: Annotated[
        str,
        Field(description="Username for SAN controller"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="Password for SAN controller"),
        SecretDictField(field="san-password"),
    ]
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class IbmgpfsBackend(StorageBackendBase):
    """GPFS backend implementation."""

    backend_type = "ibmgpfs"
    display_name = "GPFS"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-ibmgpfs"

    @property
    def charm_channel(self) -> str:
        """Return the default charm channel."""
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        """Return a pinned charm revision, if any."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the target base for this charm."""
        return "ubuntu@24.04"

    @property
    def supports_ha(self) -> bool:
        """Whether this backend supports HA deployments."""
        return False

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model type for this backend."""
        return IbmgpfsConfig
