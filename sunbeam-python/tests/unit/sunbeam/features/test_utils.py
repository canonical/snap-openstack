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
