# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend models and exceptions."""

import re
from typing import TYPE_CHECKING, Annotated, Any, Dict

import pydantic

from sunbeam.core.common import SunbeamException
from sunbeam.lazy import LazyImport

if TYPE_CHECKING:
    from cryptography import x509
else:
    x509 = LazyImport("cryptography.x509")


def validate_pem_certificates(value: str) -> str:
    """Validate raw PEM certificates or bundles without changing their content.

    This checks encoding, not certificate trust, validity dates or hostnames.
    Only certificate blocks and surrounding whitespace are accepted.
    """
    blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        value,
        flags=re.DOTALL,
    )
    remainder = re.sub(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        "",
        value,
        flags=re.DOTALL,
    )
    if not blocks or remainder.strip():
        raise ValueError("Expected PEM-encoded certificate content or CA bundle")
    try:
        for block in blocks:
            x509.load_pem_x509_certificate(block.encode("utf-8"))
    except ValueError as exc:
        raise ValueError("Invalid PEM-encoded certificate or CA bundle") from exc
    return value


PEMCertificates = Annotated[str, pydantic.AfterValidator(validate_pem_certificates)]

# =============================================================================
# Exceptions
# =============================================================================


class StorageBackendException(SunbeamException):
    """Base exception for storage backend operations."""

    pass


class BackendNotFoundException(StorageBackendException):
    """Raised when storage backend is not found."""

    pass


class BackendAlreadyExistsException(StorageBackendException):
    """Raised when storage backend already exists."""

    pass


class BackendValidationException(StorageBackendException):
    """Raised when storage backend configuration is invalid."""

    pass


# =============================================================================
# Data Models
# =============================================================================


class StorageBackendInfo(pydantic.BaseModel):
    """Information about a deployed storage backend."""

    name: str
    backend_type: str
    status: str
    charm: str
    config: Dict[str, Any] = {}


class SecretDictField:
    """Marker class to indicate a field needs to be managed as a juju secret.

    This class is used as a field annotation in Pydantic models to indicate that
    the field contains sensitive information (e.g., passwords, API tokens).

    The field name is the name of the key in the Juju secret dictionary.
    """

    def __init__(self, field: str):
        self.field = field

    def __repr__(self) -> str:
        """Return a string representation of the SecretDictField."""
        return f"SecretDictField(field={self.field})"
