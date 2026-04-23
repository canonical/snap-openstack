# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from sunbeam.features.interface.utils import (
    decode_base64_as_string,
    encode_base64_as_string,
    generate_ca_chain,
)


def test_generate_ca_chain():
    cert1 = "CERT1"
    cert2 = "CERT2"
    cert3 = "CERT3"

    ca_chain = generate_ca_chain(
        encode_base64_as_string(cert1),
        encode_base64_as_string(cert2),
        encode_base64_as_string(cert3),
    )
    ca_chain_decoded = decode_base64_as_string(ca_chain)
    expected_chain = cert1 + "\n" + cert2 + "\n" + cert3
    assert ca_chain_decoded == expected_chain


def test_generate_ca_chain_deduplicates_ca_cert_with_crlf():
    """Test that ca_certificate with CRLF endings is not duplicated when

    ca_chain already contains an equivalent certificate with LF endings.
    """
    cert1 = "CERT1"
    # ca_certificate uses CRLF line endings
    cert2_crlf = (
        "-----BEGIN CERTIFICATE-----\r\nISSUINGCA\r\n-----END CERTIFICATE-----\r\n"
    )
    # ca_chain already contains the same cert with LF line endings
    cert2_lf = "-----BEGIN CERTIFICATE-----\nISSUINGCA\n-----END CERTIFICATE-----\n"
    cert3 = "-----BEGIN CERTIFICATE-----\nROOTCA\n-----END CERTIFICATE-----\n"
    ca_chain_input = cert2_lf + "\n" + cert3

    ca_chain = generate_ca_chain(
        encode_base64_as_string(cert1),
        encode_base64_as_string(cert2_crlf),
        encode_base64_as_string(ca_chain_input),
    )
    ca_chain_decoded = decode_base64_as_string(ca_chain)
    # cert2 must appear only once in the output
    assert ca_chain_decoded.count("ISSUINGCA") == 1
    assert ca_chain_decoded.count("ROOTCA") == 1
