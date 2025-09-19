# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import logging
import typing
from pathlib import Path
from typing import Any

import pydantic
import yaml
from pydantic import Field
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ManifestItemNotFoundException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    infer_risk,
    infer_version,
)

# from sunbeam.feature_manager import FeatureManager
from sunbeam.versions import MANIFEST_CHARM_VERSIONS, TERRAFORM_DIR_NAMES

LOG = logging.getLogger(__name__)
EMPTY_MANIFEST: dict[str, dict] = {"core": {"charms": {}, "terraform": {}}}


def embedded_manifest_path(snap: Snap, version: str, risk: str) -> Path:
    return snap.paths.snap / "etc" / "manifests" / version / f"{risk}.yml"


class JujuManifest(pydantic.BaseModel):
    # Setting Field alias not supported in pydantic 1.10.0
    # Old version of pydantic is used due to dependencies
    # with older version of paramiko from python-libjuju
    # Newer version of pydantic can be used once the below
    # PR is released
    # https://github.com/juju/python-libjuju/pull/1005
    bootstrap_args: list[str] = Field(
        default=[], description="Extra args for juju bootstrap"
    )
    scale_args: list[str] = Field(
        default=[], description="Extra args for juju enable-ha"
    )
    destroy_args: list[str] = Field(
        default=[], description="Extra args for juju destroy-controller"
    )
    bootstrap_model_configs: dict[str, dict[str, Any]] = Field(
        default={},
        description="""Mapping of model to model configuration.

        This model configuration are guaranteed to be applied at model creation
        only. Only, and only if, they are not overriden by Sunbeam.
        This is offered as a convenience to allow users an easy way to
        pass initial model configuration.
        """,
    )


class CharmManifest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow")

    channel: str | None = Field(default=None, description="Channel for the charm")
    revision: int | None = Field(
        default=None, description="Revision number of the charm"
    )
    # rocks: dict[str, str] | None = Field(
    #     default=None, description="Rock images for the charm"
    # )
    config: dict[str, Any] | None = Field(
        default=None, description="Config options of the charm"
    )
    # source: Path | None = Field(
    #     default=None, description="Local charm bundle path"
    # )


class TerraformManifest(pydantic.BaseModel):
    source: Path = Field(description="Path to Terraform plan")

    @pydantic.field_serializer("source")
    def _serialize_source(self, value: Path) -> str:
        return str(value)


class SoftwareConfig(pydantic.BaseModel):
    juju: JujuManifest = JujuManifest()
    charms: dict[str, CharmManifest] = {}
    terraform: dict[str, TerraformManifest] = {}

    def validate_terraform_keys(self, default_software_config: "SoftwareConfig"):
        """Validate the terraform keys provided are expected."""
        if self.terraform:
            tf_keys = set(self.terraform.keys())
            all_tfplans = default_software_config.terraform.keys()
            if not tf_keys <= all_tfplans:
                raise ValueError(
                    f"Manifest Software Terraform keys should be one of {all_tfplans} "
                )

    def validate_charm_keys(self, default_software_config: "SoftwareConfig"):
        """Validate the charm keys provided are expected."""
        if self.charms:
            charms_keys = set(self.charms.keys())
            all_charms = default_software_config.charms.keys()
            if not charms_keys <= all_charms:
                raise ValueError(
                    f"Manifest Software charms keys should be one of {all_charms} "
                )

    def validate_against_default(
        self, default_software_config: "SoftwareConfig"
    ) -> None:
        """Validate the software config against the default software config."""
        self.validate_terraform_keys(default_software_config)
        self.validate_charm_keys(default_software_config)

    def merge(self, other: "SoftwareConfig") -> "SoftwareConfig":
        """Return a merged version of the software config."""
        juju = JujuManifest.model_validate(
            utils.merge_dict(
                self.juju.model_dump(by_alias=True),
                other.juju.model_dump(by_alias=True),
            )
        )
        charms: dict[str, CharmManifest] = utils.merge_dict(
            copy.deepcopy(self.charms), copy.deepcopy(other.charms)
        )
        terraform: dict[str, TerraformManifest] = utils.merge_dict(
            copy.deepcopy(self.terraform), copy.deepcopy(other.terraform)
        )
        return SoftwareConfig(juju=juju, charms=charms, terraform=terraform)


class FeatureConfig(pydantic.BaseModel):
    pass


def _default_software_config() -> SoftwareConfig:
    snap = Snap()
    return SoftwareConfig(
        charms={
            charm: CharmManifest(channel=channel)
            for charm, channel in MANIFEST_CHARM_VERSIONS.items()
        },
        terraform={
            tfplan: TerraformManifest(source=snap.paths.snap / "etc" / tfplan_dir)
            for tfplan, tfplan_dir in TERRAFORM_DIR_NAMES.items()
        },
    )


def _str_serialize(value: Any | None) -> str | None:
    if value is not None:
        return str(value)
    return None


class CoreConfig(pydantic.BaseModel):
    class _ProxyConfig(pydantic.BaseModel):
        proxy_required: bool | None = None
        http_proxy: str | None = None
        https_proxy: str | None = None
        no_proxy: str | None = None

    class _BootstrapConfig(pydantic.BaseModel):
        management_cidr: str | None = pydantic.Field(
            default=None, description="Management network CIDR"
        )

    class _Addons(pydantic.BaseModel):
        metallb: str | None = None

    class _K8sAddons(pydantic.BaseModel):
        loadbalancer: str | None = None

    class _User(pydantic.BaseModel):
        run_demo_setup: bool | None = None
        username: str | None = None
        password: str | None = None
        cidr: str | None = None
        nameservers: str | None = None
        security_group_rules: bool | None = None
        remote_access_location: typing.Literal["local", "remote"] | None = None

    class _ExternalNetwork(pydantic.BaseModel):
        nic: str | None = pydantic.Field(
            None, deprecated="Deprecated. Use `nics` instead."
        )
        nics: dict[str, str] | None = pydantic.Field(
            None, description="Mapping of key hostname to nic name."
        )
        cidr: str | None = None
        gateway: str | None = None
        range: str | None = None
        network_type: typing.Literal["vlan", "flat"] | None = None
        segmentation_id: int | None = None

    class _HostMicroCephConfig(pydantic.BaseModel):
        osd_devices: list[str] | None = None

        @pydantic.field_validator("osd_devices", mode="before")
        @classmethod
        def _validate_osd_devices(cls, v):
            if isinstance(v, str):
                return v.split(",")
            return v

    class _Identity(pydantic.BaseModel):
        class _IdentitySAML2KeyAndCert(pydantic.BaseModel):
            certificate: str
            key: str

        class _IdentityProfile(pydantic.BaseModel):
            provider: str
            protocol: str
            config: dict[str, str]

        profiles: dict[str, _IdentityProfile]
        saml2_x509: _IdentitySAML2KeyAndCert

    class _PCI(pydantic.BaseModel):
        # Source: https://docs.openstack.org/nova/latest/configuration/config.html#pci.device_spec
        device_specs: list[dict[str, Any]] | None = None
        # https://docs.openstack.org/nova/latest/configuration/config.html#pci.alias
        aliases: list[dict[str, Any]] | None = None
        # Excluded PCI addresses per node.
        excluded_devices: dict[str, list[str]] | None = None

    class _Endpoints(pydantic.BaseModel):
        class _Endpoint(pydantic.BaseModel):
            hostname: str | None = None
            ip: pydantic.IPvAnyAddress | None = None

        ingress_internal: _Endpoint | None = pydantic.Field(
            None, alias="ingress-internal"
        )
        ingress_public: _Endpoint | None = pydantic.Field(None, alias="ingress-public")
        ingress_rgw: _Endpoint | None = pydantic.Field(None, alias="ingress-rgw")

    class _DPDK(pydantic.BaseModel):
        enabled: bool = False
        datapath_cores: int = 0
        control_plane_cores: int = 0
        # MB
        memory: int = 0
        driver: str = "vfio-pci"
        ports: dict[str, list[str]]

    proxy: _ProxyConfig | None = None
    bootstrap: _BootstrapConfig | None = None
    database: str | None = None
    region: str | None = None
    addons: _Addons | None = None
    identity: _Identity | None = None
    k8s_addons: _K8sAddons | None = pydantic.Field(default=None, alias="k8s-addons")
    endpoints: _Endpoints | None = None
    user: _User | None = None
    external_network: _ExternalNetwork | None = None
    microceph_config: pydantic.RootModel[dict[str, _HostMicroCephConfig]] | None = None
    pci: _PCI | None = None
    dpdk: _DPDK | None = None


class CoreManifest(pydantic.BaseModel):
    config: CoreConfig = CoreConfig()
    software: SoftwareConfig = pydantic.Field(default_factory=_default_software_config)

    def merge(self, other: "CoreManifest") -> "CoreManifest":
        """Merge the core manifest with the provided manifest."""
        config = CoreConfig.model_validate(
            utils.merge_dict(
                self.config.model_dump(by_alias=True),
                other.config.model_dump(by_alias=True),
            )
        )
        software = self.software.merge(other.software)
        return type(self)(config=config, software=software)


class FeatureManifest(pydantic.BaseModel):
    config: pydantic.SerializeAsAny[FeatureConfig] | None = None
    software: SoftwareConfig = SoftwareConfig()

    def merge(self, other: "FeatureManifest") -> "FeatureManifest":
        """Merge the feature manifest with the provided manifest."""
        if self.config and other.config:
            if type(self.config) is not type(other.config):
                raise ValueError("Feature config types do not match")
            config = type(self.config).model_validate(
                utils.merge_dict(
                    self.config.model_dump(by_alias=True),
                    other.config.model_dump(by_alias=True),
                )
            )
        elif other.config:
            config = other.config
        elif self.config:
            config = self.config
        else:
            config = None
        software = self.software.merge(other.software)
        return type(self)(config=config, software=software)


class FeatureGroupManifest(pydantic.RootModel[dict[str, FeatureManifest]]):
    def merge(self, other: "FeatureGroupManifest") -> "FeatureGroupManifest":
        """Merge the feature group manifest with the provided manifest."""
        features = {}
        for feature, feature_manifest in self.root.items():
            if other_manifest := other.root.get(feature):
                features[feature] = feature_manifest.merge(other_manifest)
            else:
                features[feature] = feature_manifest

        return type(self)(root=features)

    def validate_againt_default(self, default_manifest: "FeatureGroupManifest") -> None:
        """Validate the feature group manifest against the default manifest."""
        for feature, feature_manifest in self.root.items():
            if other_manifest := default_manifest.root.get(feature):
                feature_manifest.software.validate_against_default(
                    other_manifest.software
                )


class Manifest(pydantic.BaseModel):
    core: CoreManifest = pydantic.Field(default_factory=CoreManifest)
    features: dict[str, FeatureManifest | FeatureGroupManifest] = {}

    def get_features(self) -> typing.Generator[tuple[str, FeatureManifest], None, None]:
        """Return all the features."""
        for name, feature in self.features.items():
            if isinstance(feature, FeatureGroupManifest):
                yield from feature.root.items()
            else:
                yield name, feature

    def get_feature(self, name: str) -> FeatureManifest | None:
        """Return the feature."""
        for f_o_g_name, feature_or_group_manifest in self.features.items():
            if f_o_g_name == name and isinstance(
                feature_or_group_manifest, FeatureManifest
            ):
                return feature_or_group_manifest
            if isinstance(feature_or_group_manifest, FeatureGroupManifest):
                for (
                    feature_name,
                    feature_manifest,
                ) in feature_or_group_manifest.root.items():
                    if feature_name == name:
                        return feature_manifest
        return None

    @classmethod
    def from_file(cls, file: Path) -> "Manifest":
        """Load manifest from file."""
        with file.open() as f:
            return cls.model_validate(yaml.safe_load(f))

    def merge(self, other: "Manifest") -> "Manifest":
        """Merge the manifest with the provided manifest."""
        core = self.core.merge(other.core)
        features: dict[str, FeatureManifest | FeatureGroupManifest] = {}
        for feature, feature_or_group_manifest in self.features.items():
            if other_manifest := other.features.get(feature):
                if isinstance(feature_or_group_manifest, FeatureGroupManifest):
                    if not isinstance(other_manifest, FeatureGroupManifest):
                        raise ValueError("Feature group and feature do not match")
                    features[feature] = feature_or_group_manifest.merge(other_manifest)
                elif isinstance(feature_or_group_manifest, FeatureManifest):
                    if not isinstance(other_manifest, FeatureManifest):
                        raise ValueError("Feature and feature group do not match")
                    features[feature] = feature_or_group_manifest.merge(other_manifest)
            else:
                features[feature] = feature_or_group_manifest

        return type(self)(core=core, features=features)

    def validate_against_default(self, default_manifest: "Manifest") -> None:
        """Validate the manifest against the default manifest."""
        self.core.software.validate_against_default(default_manifest.core.software)
        for feature, feature_or_group_manifest in self.features.items():
            if other_manifest := default_manifest.features.get(feature):
                if isinstance(feature_or_group_manifest, FeatureGroupManifest):
                    if not isinstance(other_manifest, FeatureGroupManifest):
                        raise ValueError("Feature group and feature do not match")
                    feature_or_group_manifest.validate_againt_default(other_manifest)
                elif isinstance(feature_or_group_manifest, FeatureManifest):
                    if not isinstance(other_manifest, FeatureManifest):
                        raise ValueError("Feature and feature group do not match")
                    feature_or_group_manifest.software.validate_against_default(
                        other_manifest.software
                    )


class AddManifestStep(BaseStep):
    """Add Manifest file to cluster database.

    This step writes the manifest file to cluster database if:
    - The user provides a manifest file.
    - The user clears the manifest.
    - The risk level is not stable.
    Any other reason will be skipped.
    """

    manifest_content: dict[str, dict] | None

    def __init__(
        self,
        client: Client,
        manifest_file: Path | None = None,
        clear: bool = False,
    ):
        super().__init__("Write Manifest to database", "Writing Manifest to database")
        self.client = client
        self.manifest_file = manifest_file
        self.clear = clear
        self.manifest_content = None
        self.snap = Snap()

    def is_skip(self, status: Status | None = None) -> Result:
        """Skip if the user provided manifest and the latest from db are same."""
        risk = infer_risk(self.snap)
        version = infer_version(self.snap)
        try:
            embedded_manifest = yaml.safe_load(
                embedded_manifest_path(self.snap, version, risk).read_bytes()
            )
            if self.manifest_file:
                with self.manifest_file.open("r") as file:
                    self.manifest_content = yaml.safe_load(file)
            elif self.clear:
                self.manifest_content = EMPTY_MANIFEST
        except (yaml.YAMLError, IOError) as e:
            LOG.debug("Failed to load manifest", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        latest_manifest = None
        try:
            latest_manifest = self.client.cluster.get_latest_manifest()
        except ManifestItemNotFoundException:
            if self.manifest_content is None:
                self.manifest_content = embedded_manifest
        except ClusterServiceUnavailableException as e:
            LOG.debug("Failed to fetch latest manifest from clusterd", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        if self.manifest_content is None:
            return Result(ResultType.SKIPPED)

        if (
            latest_manifest
            and yaml.safe_load(latest_manifest.get("data", {})) == self.manifest_content
        ):
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Write manifest to cluster db."""
        try:
            id = self.client.cluster.add_manifest(
                data=yaml.safe_dump(self.manifest_content)
            )
            return Result(ResultType.COMPLETED, id)
        except Exception as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))
