# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Validation and serialization of storage network settings."""

import json
from unittest.mock import Mock, patch

import pytest

from sunbeam.storage.backends.purestorage.backend import PureStorageBackend
from sunbeam.storage.steps import prompt_backend_config


@pytest.mark.parametrize(
    "field",
    [
        "pure_iscsi_cidr",
        "pure_nvme_cidr",
        "pure_iscsi_cidr_list",
        "pure_nvme_cidr_list",
    ],
)
@pytest.mark.parametrize(
    "network", ["192.0.2.0/24", "2001:db8::/64", "192.0.2.1/24", "not-a-network"]
)
def test_network_config_roundtrip(field, network):
    """CIDR strings pass through without constraints absent from the snap schema."""
    backend = PureStorageBackend()
    value = f"{network}, 198.51.100.0/24" if field.endswith("_list") else network
    preseed = {
        "san_ip": "192.0.2.10",
        "pure_api_token": "test-token",
        "protocol": "iscsi",
        field: value,
    }
    with (
        patch("sunbeam.storage.steps.load_answers", return_value={}),
        patch("sunbeam.storage.steps.write_answers") as write_answers,
    ):
        answers = prompt_backend_config(
            Mock(),
            backend,
            Mock(storage=None),
            "pure-test",
            preseed,
            accept_defaults=True,
        )
    json.dumps(write_answers.call_args.args[2])
    model = backend.config_type().model_validate(answers, by_name=True)
    assert getattr(model, field) == value
    tfvars = backend.build_terraform_vars(
        Mock(), Mock(storage=Mock(root={})), "pure-test", model
    )
    assert tfvars["charm_config"][field.replace("_", "-")] == value
    assert tfvars["charm_config"]["san-ip"] == "192.0.2.10"
    json.dumps(tfvars["charm_config"])
