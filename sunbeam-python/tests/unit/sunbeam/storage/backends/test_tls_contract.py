# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Cross-backend storage TLS UX contract tests."""

from dataclasses import dataclass

import click
import pytest
from click.testing import CliRunner

from sunbeam.storage.cli_base import StorageBackendCLIBase
from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.conftest import BACKENDS

GENERIC_CERT_BACKENDS = (
    "dellpowerstore",
    "dellpowervault",
    "hitachi",
    "purestorage",
    "stx",
)


@dataclass(frozen=True)
class TLSField:
    """Expected user-facing and transport contract for a TLS-related field."""

    backend: str
    field: str
    is_boolean: bool = False
    is_secret: bool = False
    is_path: bool = False

    @property
    def alias(self) -> str:
        """Return the charm configuration alias."""
        return self.field.replace("_", "-")


TLS_FIELDS = (
    *(TLSField(backend, "driver_ssl_cert") for backend in GENERIC_CERT_BACKENDS),
    TLSField("hitachi", "hitachi_mirror_ssl_cert"),
    TLSField("nimble", "nimble_verify_certificate", is_boolean=True),
    TLSField("nimble", "nimble_verify_cert_path"),
    TLSField("netapp", "netapp_ssl_cert_path"),
    TLSField("netapp", "netapp_private_key_file", is_secret=True),
    TLSField("netapp", "netapp_certificate_file"),
    TLSField("netapp", "netapp_ca_certificate_file"),
    TLSField("netapp", "netapp_certificate_host_validation", is_boolean=True),
    TLSField("infinidat", "driver_use_ssl", is_boolean=True),
    TLSField("qnap", "driver_ssl_cert_verify", is_boolean=True),
    TLSField("solidfire", "driver_ssl_cert_verify", is_boolean=True),
    TLSField("dellsc", "dell_sc_verify_cert", is_boolean=True),
    TLSField("synology", "synology_ssl_verify", is_boolean=True),
    TLSField("zadara", "zadara_vpsa_use_ssl", is_boolean=True),
    TLSField("zadara", "zadara_ssl_cert_verify", is_boolean=True),
    TLSField("dellsc", "san_private_key", is_path=True),
    TLSField("fujitsueternusdx", "fujitsu_private_key_path", is_path=True),
    TLSField("ibmgpfs", "gpfs_private_key", is_path=True),
    TLSField("ibmgpfs", "gpfs_hosts_key_file", is_path=True),
)


def _backend(name):
    """Return a backend instance by registry name."""
    return BACKENDS[name]()


def _field_is_secret(field_info) -> bool:
    """Return whether a model field uses Juju secret mapping."""
    return any(
        isinstance(metadata, SecretDictField) for metadata in field_info.metadata
    )


def _add_options(backend) -> dict[str, click.Option]:
    """Return generated add-command options keyed by Click parameter name."""
    params = StorageBackendCLIBase(backend)._build_add_params()
    return {param.name: param for param in params if isinstance(param, click.Option)}


@pytest.mark.parametrize("backend_name", GENERIC_CERT_BACKENDS)
def test_generic_certificate_is_pem_content_in_manifest(backend_name):
    """Generic certificate fields accept content and appear in the schema."""
    backend = _backend(backend_name)
    field = backend.config_type().model_fields["driver_ssl_cert"]

    assert "PEM" in field.description
    assert "path" not in field.description.lower()
    assert (
        "driver-ssl-cert"
        in backend.config_type().model_json_schema(by_alias=True)["properties"]
    )


def test_nimble_certificate_bundle_is_described_as_content():
    """Nimble asks for PEM bundle content instead of a local path."""
    backend = _backend("nimble")
    description = (
        backend.config_type().model_fields["nimble_verify_cert_path"].description
    )

    assert "PEM" in description
    assert "content" in description.lower()
    assert "path" not in description.lower()


@pytest.mark.parametrize("contract", TLS_FIELDS)
def test_tls_field_manifest_type_and_secret_classification(contract):
    """Manifest schemas and secret markers match the TLS contract."""
    backend = _backend(contract.backend)
    model = backend.config_type()
    field = model.model_fields[contract.field]
    schema = model.model_json_schema(by_alias=True)["properties"][contract.alias]
    schema_text = str(schema)

    assert ("boolean" in schema_text) is contract.is_boolean
    assert _field_is_secret(field) is contract.is_secret
    if contract.is_path:
        assert any(word in field.description.lower() for word in ("file", "path"))


@pytest.mark.parametrize("contract", TLS_FIELDS)
def test_generated_cli_exposes_tls_field_with_model_help(contract):
    """Generated add commands preserve model types and descriptions."""
    backend = _backend(contract.backend)
    field = backend.config_type().model_fields[contract.field]
    option = _add_options(backend)[contract.field]

    expected_type = click.BOOL if contract.is_boolean else click.STRING
    assert option.type is expected_type
    assert option.help == field.description
    if not contract.is_boolean and not contract.is_path:
        assert "PEM" in option.help
        assert "path" not in option.help.lower()


@pytest.mark.parametrize("contract", TLS_FIELDS)
def test_registered_cli_help_exposes_tls_field(contract):
    """Registered add commands render each TLS option in actual Click help."""
    backend = _backend(contract.backend)

    @click.group()
    def add():
        """Test storage add group."""

    StorageBackendCLIBase(backend).register_add_cli(add)
    result = CliRunner().invoke(add, [backend.backend_type, "--help"])

    assert result.exit_code == 0, result.output
    assert f"--{contract.alias}" in result.output


@pytest.mark.parametrize("contract", TLS_FIELDS)
def test_terraform_places_tls_field_in_config_or_secret(
    contract, mock_deployment, mock_manifest
):
    """Terraform uses ordinary config except for raw private-key material."""
    backend = _backend(contract.backend)
    marker = True if contract.is_boolean else f"{contract.alias}-value"
    config = backend.config_type().model_construct(**{contract.field: marker})

    tfvars = backend.build_terraform_vars(
        mock_deployment, mock_manifest, "tls-audit", config
    )

    assert tfvars["charm_config"][contract.alias] == marker
    assert (contract.alias in tfvars["secrets"]) is contract.is_secret
    if contract.is_secret:
        assert tfvars["secrets"][contract.alias] == contract.alias
