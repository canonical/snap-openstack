# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NetApp backend."""

import pytest

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNetAppBackend(BaseBackendTests):
    """Tests for NetApp backend."""

    @pytest.fixture
    def backend(self, netapp_backend):
        """Provide NetApp backend instance."""
        return netapp_backend

    def test_backend_type_is_netapp(self, backend):
        """Test that backend type is 'netapp'."""
        assert backend.backend_type == "netapp"

    def test_charm_name_is_netapp_charm(self, backend):
        """Test that charm name is cinder-volume-netapp."""
        assert backend.charm_name == "cinder-volume-netapp"

    def test_certificate_options_accept_pem_content(self, backend):
        """Test that public certificate options describe PEM content input."""
        fields = backend.config_type().model_fields
        for field_name in (
            "netapp_ssl_cert_path",
            "netapp_certificate_file",
            "netapp_ca_certificate_file",
        ):
            assert "PEM content" in fields[field_name].description

    def test_only_private_tls_material_is_secret(self, backend):
        """Test that only the private key uses Juju secret handling."""
        fields = backend.config_type().model_fields
        public_fields = (
            "netapp_ssl_cert_path",
            "netapp_certificate_file",
            "netapp_ca_certificate_file",
        )
        for field_name in public_fields:
            assert not any(
                isinstance(metadata, SecretDictField)
                for metadata in fields[field_name].metadata
            )

        private_key_metadata = fields["netapp_private_key_file"].metadata
        private_key_secret = next(
            metadata
            for metadata in private_key_metadata
            if isinstance(metadata, SecretDictField)
        )
        assert private_key_secret.field == "netapp-private-key-file"

    def test_private_key_accepts_pem_content_via_juju_secret(self, backend):
        """Test that the private key UX describes its content and transport."""
        description = (
            backend.config_type().model_fields["netapp_private_key_file"].description
        )
        assert "PEM content" in description
        assert "Juju secret" in description

    def test_tls_material_terraform_mapping(
        self, backend, mock_deployment, mock_manifest
    ):
        """Test public PEM content and the private key use their intended paths."""
        ca_bundle = "-----BEGIN CERTIFICATE-----\nbundle\n-----END CERTIFICATE-----"
        certificate = "-----BEGIN CERTIFICATE-----\nclient\n-----END CERTIFICATE-----"
        ca_certificate = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
        private_key = "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----"
        config = backend.config_type().model_validate(
            {
                "san-ip": "192.0.2.10",
                "protocol": "iscsi",
                "netapp-ssl-cert-path": ca_bundle,
                "netapp-certificate-file": certificate,
                "netapp-ca-certificate-file": ca_certificate,
                "netapp-private-key-file": private_key,
            }
        )

        tfvars = backend.build_terraform_vars(
            mock_deployment, mock_manifest, "test-netapp", config
        )

        assert tfvars["charm_config"]["netapp-ssl-cert-path"] == ca_bundle
        assert tfvars["charm_config"]["netapp-certificate-file"] == certificate
        assert tfvars["charm_config"]["netapp-ca-certificate-file"] == ca_certificate
        assert tfvars["secrets"]["netapp-private-key-file"] == "netapp-private-key-file"
        assert "netapp-certificate-file" not in tfvars["secrets"]
        assert "netapp-ssl-cert-path" not in tfvars["secrets"]
        assert "netapp-ca-certificate-file" not in tfvars["secrets"]
