# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common base test class for all storage backends.

This module provides a base test class that can be inherited by backend-specific
test classes to ensure all backends implement the required interface correctly.
"""

import ipaddress
import json

import pytest
import yaml
from pydantic import BaseModel, IPvAnyAddress, TypeAdapter, ValidationError

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

# IP constraints from canonical/snap-cinder-volume, cinder_volume/configuration.py
SNAP_IP_BACKENDS = {
    "datera",
    "dellpowermax",
    "dellpowerstore",
    "dellsc",
    "dellunity",
    "hitachi",
    "hpe3par",
    "ibmflashsystemcommon",
    "ibmgpfs",
    "ibmibmstorage",
    "ibmstorwizesvc",
    "infinidat",
    "inspuras13000",
    "inspurinstorage",
    "nimble",
    "purestorage",
    "qnap",
    "solidfire",
    "toyouacs5000",
}
SNAP_STRING_BACKENDS = {
    "datacore",
    "dellpowervault",
    "fujitsueternusdx",
    "hpexp",
    "huawei",
    "ibmflashsystemiscsi",
    "kaminario",
    "linstor",
    "macrosan",
    "necv",
    "netapp",
    "nexenta",
    "opene",
    "prophetstor",
    "sandstone",
    "stx",
    "synology",
    "veritasaccess",
    "yadro",
    "zadara",
}


class BaseBackendTests:
    """Base test class for all storage backends.

    This class provides common tests that verify each backend implements
    the required interface and behaves correctly. Backend-specific test
    classes should inherit from this class and override the `backend` fixture
    to provide their specific backend instance.

    Example:
        class TestHitachiBackend(BaseBackendTests):
            @pytest.fixture
            def backend(self, hitachi_backend):
                return hitachi_backend
    """

    @pytest.fixture
    def backend(self):
        """Override this fixture in subclasses to provide the backend instance.

        Raises:
            NotImplementedError: If not overridden in subclass.
        """
        raise NotImplementedError(
            "Subclasses must override the backend fixture to provide a backend instance"
        )

    # Core attribute tests

    def test_backend_has_type(self, backend):
        """Test that backend has a type identifier."""
        assert hasattr(backend, "backend_type")
        assert isinstance(backend.backend_type, str)
        assert len(backend.backend_type) > 0

    def test_backend_has_display_name(self, backend):
        """Test that backend has a display name."""
        assert hasattr(backend, "display_name")
        assert isinstance(backend.display_name, str)
        assert len(backend.display_name) > 0

    def test_backend_is_storage_backend_base(self, backend):
        """Test that backend inherits from StorageBackendBase."""
        assert isinstance(backend, StorageBackendBase)

    # Charm property tests

    def test_charm_name_is_set(self, backend):
        """Test that charm_name property is set."""
        assert backend.charm_name
        assert isinstance(backend.charm_name, str)
        assert backend.charm_name.startswith("cinder-volume-")

    def test_charm_channel_is_set(self, backend):
        """Test that charm_channel property is set."""
        assert backend.charm_channel
        assert isinstance(backend.charm_channel, str)

    def test_charm_base_is_set(self, backend):
        """Test that charm_base property is set."""
        assert backend.charm_base
        assert isinstance(backend.charm_base, str)

    # Configuration tests

    def test_config_type_returns_class(self, backend):
        """Test that config_type() returns a class."""
        config_class = backend.config_type()
        assert isinstance(config_class, type)

    def test_config_type_is_storage_backend_config(self, backend):
        """Test that config_type() returns a StorageBackendConfig subclass."""
        config_class = backend.config_type()
        assert issubclass(config_class, StorageBackendConfig)

    def test_config_type_is_pydantic_model(self, backend):
        """Test that config_type() returns a Pydantic model."""
        config_class = backend.config_type()
        assert issubclass(config_class, BaseModel)

    # Required field tests

    def test_config_has_san_ip_field(self, backend):
        """Test that config has san_ip field."""
        config_class = backend.config_type()
        assert "san_ip" in config_class.model_fields

    @pytest.mark.parametrize("address", ["192.0.2.10", "2001:db8::10"])
    def test_san_ip_validates_and_serializes(self, backend, address):
        """Match downstream field types and dump addresses as wire strings."""
        model = backend.config_type()
        field = model.model_fields["san_ip"]
        assert backend.backend_type in SNAP_IP_BACKENDS | SNAP_STRING_BACKENDS
        requires_ip = backend.backend_type in SNAP_IP_BACKENDS
        assert field.annotation is (IPvAnyAddress if requires_ip else str)
        validated = TypeAdapter(field.annotation).validate_python(address)
        assert validated == (ipaddress.ip_address(address) if requires_ip else address)
        config = model.model_construct(san_ip=validated)
        dumped = config.model_dump(by_alias=True, exclude_unset=True)
        assert dumped == {"san-ip": address}
        assert json.loads(json.dumps(dumped)) == dumped
        assert yaml.safe_load(yaml.safe_dump(dumped)) == dumped

    @pytest.mark.parametrize("address", ["192.0.2.10d2d", "300.1.2.3", "host.test"])
    def test_san_ip_matches_downstream_validation(self, backend, address):
        """Only impose IP constraints where the downstream model defines them."""
        if backend.backend_type in SNAP_STRING_BACKENDS:
            model = backend.config_type()
            value = TypeAdapter(
                model.model_fields["san_ip"].annotation
            ).validate_python(address)
            assert value == address
            # Exercise model validation too; missing unrelated required fields
            # must not introduce an IP validation failure for a string option.
            try:
                config = model.model_validate({"san-ip": address})
            except ValidationError as exc:
                assert all(error["loc"] != ("san-ip",) for error in exc.errors())
            else:
                assert config.san_ip == address
            return
        assert backend.backend_type in SNAP_IP_BACKENDS
        with pytest.raises(ValidationError) as exc:
            backend.config_type().model_validate({"san-ip": address})
        assert any(
            error["loc"] == ("san-ip",) and error["type"] == "ip_any_address"
            for error in exc.value.errors()
        )

    def test_config_has_protocol_field(self, backend):
        """Test that config has protocol field."""
        config_class = backend.config_type()
        # Protocol field should exist
        assert "protocol" in config_class.model_fields

    # Method existence tests

    def test_has_get_endpoint_bindings_method(self, backend):
        """Test that backend has get_endpoint_bindings method."""
        assert hasattr(backend, "get_endpoint_bindings")
        assert callable(backend.get_endpoint_bindings)

    def test_has_validate_ip_or_fqdn_static_method(self, backend):
        """Test that backend class has _validate_ip_or_fqdn static method."""
        # This is a static method on the base class
        assert hasattr(StorageBackendBase, "_validate_ip_or_fqdn")
        assert callable(StorageBackendBase._validate_ip_or_fqdn)

    def test_has_register_terraform_plan_method(self, backend):
        """Test that backend has register_terraform_plan method."""
        assert hasattr(backend, "register_terraform_plan")
        assert callable(backend.register_terraform_plan)

    def test_has_add_backend_instance_method(self, backend):
        """Test that backend has add_backend_instance method."""
        assert hasattr(backend, "add_backend_instance")
        assert callable(backend.add_backend_instance)

    def test_has_remove_backend_method(self, backend):
        """Test that backend has remove_backend method."""
        assert hasattr(backend, "remove_backend")
        assert callable(backend.remove_backend)

    def test_has_build_terraform_vars_method(self, backend):
        """Test that backend has build_terraform_vars method."""
        assert hasattr(backend, "build_terraform_vars")
        assert callable(backend.build_terraform_vars)


class TestAllBackends(BaseBackendTests):
    """Test all backends using parametrized fixture.

    This class runs the common tests against all backends to ensure
    they all implement the required interface consistently.
    """

    @pytest.fixture
    def backend(self, any_backend):
        """Use the parametrized any_backend fixture."""
        return any_backend


# Backend uniqueness tests


def test_all_backends_have_unique_types():
    """Test that all backends have unique type identifiers."""
    from tests.unit.sunbeam.storage.backends.conftest import BACKENDS

    backends = [cls() for cls in BACKENDS.values()]
    types = [b.backend_type for b in backends]

    # Check no duplicates
    assert len(types) == len(set(types)), f"Duplicate backend types found: {types}"


def test_all_backends_have_unique_charm_names():
    """Test that all backends have unique charm names."""
    from tests.unit.sunbeam.storage.backends.conftest import BACKENDS

    backends = [cls() for cls in BACKENDS.values()]
    charm_names = [b.charm_name for b in backends]

    # Check no duplicates
    assert len(charm_names) == len(set(charm_names)), (
        f"Duplicate charm names found: {charm_names}"
    )


def test_backend_types_match_expected():
    """Test that each backend's type matches its registry key."""
    from tests.unit.sunbeam.storage.backends.conftest import BACKENDS

    for key, cls in BACKENDS.items():
        backend = cls()
        assert backend.backend_type == key, (
            f"Backend registered as '{key}' has type '{backend.backend_type}'"
        )


def test_backend_charm_names_match_expected():
    """Test that each backend's charm name follows convention."""
    from tests.unit.sunbeam.storage.backends.conftest import BACKENDS

    for key, cls in BACKENDS.items():
        backend = cls()
        expected_charm = f"cinder-volume-{key}"
        assert backend.charm_name == expected_charm, (
            f"Backend '{key}' has charm_name '{backend.charm_name}', "
            f"expected '{expected_charm}'"
        )
