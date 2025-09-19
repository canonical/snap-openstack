# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import enum
import logging
import pathlib
import shutil
from typing import TYPE_CHECKING, Type
from urllib.request import proxy_bypass

import pydantic
import yaml
from snaphelpers import Snap

import sunbeam.utils as sunbeam_utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    RiskLevel,
    _get_default_no_proxy_settings,
    infer_risk,
    infer_version,
    read_config,
)
from sunbeam.core.juju import JujuAccount, JujuController
from sunbeam.core.manifest import (
    FeatureGroupManifest,
    FeatureManifest,
    Manifest,
    embedded_manifest_path,
)
from sunbeam.core.proxy import patch_process_env, should_bypass
from sunbeam.core.terraform import TerraformHelper
from sunbeam.versions import MANIFEST_ATTRIBUTES_TFVAR_MAP, TERRAFORM_DIR_NAMES

if TYPE_CHECKING:
    from sunbeam.feature_manager import FeatureManager
    from sunbeam.features.interface.v1.base import BaseFeature
else:
    FeatureManager = object
    BaseFeature = object

LOG = logging.getLogger(__name__)
PROXY_CONFIG_KEY = "ProxySettings"

_cls_registry: dict[str, Type["Deployment"]] = {}


def register_deployment_type(type_: str, cls: Type["Deployment"]):
    global _cls_registry
    _cls_registry[type_] = cls


def get_deployment_class(type_: str) -> Type["Deployment"]:
    global _cls_registry
    return _cls_registry[type_]


class MissingTerraformInfoException(Exception):
    """An Exception raised when terraform information is missing in manifest."""

    pass


class Networks(enum.Enum):
    PUBLIC = "public"
    STORAGE = "storage"
    STORAGE_CLUSTER = "storage-cluster"
    INTERNAL = "internal"
    DATA = "data"
    MANAGEMENT = "management"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class CertPair(pydantic.BaseModel):
    certificate: str
    private_key: str = pydantic.Field(
        validation_alias=pydantic.AliasChoices("private_key", "private-key"),
        serialization_alias="private-key",
    )


class Deployment(pydantic.BaseModel):
    name: str
    url: str
    type: str
    juju_account: JujuAccount | None = None
    juju_controller: JujuController | None = None
    clusterd_certpair: CertPair | None = None
    _manifest: Manifest | None = pydantic.PrivateAttr(default=None)
    _tfhelpers: dict[str, TerraformHelper] = pydantic.PrivateAttr(default={})
    _feature_manager: FeatureManager | None = pydantic.PrivateAttr(default=None)

    @property
    def openstack_machines_model(self) -> str:
        """Return the openstack machines model name."""
        return NotImplemented

    @property
    def controller(self) -> str:
        """Return controller name."""
        return NotImplemented

    @classmethod
    def load(cls, deployment: dict) -> "Deployment":
        """Load deployment from dict."""
        if type_ := deployment.get("type"):
            return _cls_registry.get(type_, Deployment)(**deployment)
        raise ValueError("Deployment type not set.")

    @classmethod
    def import_step(cls) -> Type:
        """Return a step for importing a deployment.

        This step will be used to make sure the deployment is valid.
        The step must take as constructor arguments: DeploymentsConfig, Deployment.
        The Deployment must be of the type that the step is registered for.
        """
        raise NotImplementedError

    def get_client(self) -> Client:
        """Return a client instance.

        Raises ValueError when fails to instantiate a client.
        """
        raise NotImplementedError

    def get_clusterd_http_address(self) -> str:
        """Return the address of the clusterd server."""
        raise NotImplementedError

    def generate_core_config(self, console) -> str:
        """Generate preseed for deployment."""
        return NotImplemented

    def _ensure_juju_controller_in_no_proxy(self, juju_controller: JujuController):
        """Ensure juju controller is in no_proxy settings.

        Manual intervention is necessary for Juju Controller only.
        Underlying websocket library does not support CIDR notation for
        no_proxy.
        """
        proxy = self.get_proxy_settings()
        if not proxy:
            return
        no_proxy = proxy.get("NO_PROXY", "").strip()
        no_proxy_set: set[str] = set(filter(None, no_proxy.split(",")))
        for endpoint in juju_controller.api_endpoints:
            # Defined environment proxy does allow for the controller ip to be bypassed.
            if not should_bypass(no_proxy_set, endpoint):
                LOG.debug("Endpoint %s should not bypass proxy, skipping", endpoint)
                continue
            # Defined environment proxy allows for the controller ip to be bypassed.
            if not proxy_bypass(endpoint, proxies={"no": ",".join(no_proxy_set)}):
                # The stdlib function does not consider it should be bypassed.
                host = endpoint.rsplit(":", 1)[0]
                LOG.debug(
                    "Endpoint %s should bypass proxy, adding to no_proxy", endpoint
                )
                no_proxy_set.add(host)
        proxy["NO_PROXY"] = ",".join(no_proxy_set)
        self.ensure_proxy_settings(proxy)

    def ensure_proxy_settings(self, settings: dict[str, str] | None = None):
        """Ensure current env has proxy settings."""
        if not settings:
            settings = self.get_proxy_settings()
        patch_process_env(settings)

    def get_default_proxy_settings(self) -> dict:
        """Return default proxy settings."""
        return {}

    def get_feature_manager(self) -> "FeatureManager":
        """Return the feature manager for the deployment."""
        from sunbeam.feature_manager import FeatureManager

        if self._feature_manager is None:
            self._feature_manager = FeatureManager()

        return self._feature_manager

    def get_proxy_settings(self) -> dict:
        """Fetch proxy settings from clusterd, if not available use defaults."""
        proxy = {}
        try:
            # If client does not exist, use detaults
            client = self.get_client()
            proxy_from_db = read_config(client, PROXY_CONFIG_KEY).get("proxy", {})
            if proxy_from_db.get("proxy_required"):
                proxy = {
                    p.upper(): v
                    for p in ("http_proxy", "https_proxy", "no_proxy")
                    if (v := proxy_from_db.get(p))
                }
        except (
            ClusterServiceUnavailableException,
            ConfigItemNotFoundException,
            ValueError,
        ) as e:
            LOG.debug(f"Using default Proxy settings from provider due to {str(e)}")
            proxy = self.get_default_proxy_settings()

        if "NO_PROXY" in proxy:
            no_proxy_list = set(proxy.get("NO_PROXY", "").split(","))
            default_no_proxy_list = _get_default_no_proxy_settings()
            proxy["NO_PROXY"] = ",".join(no_proxy_list.union(default_no_proxy_list))

        return proxy

    def parse_feature_manifest(self, feature_manifest_data: dict[str, dict]) -> dict:
        """Parse feature manifest data."""
        if not feature_manifest_data:
            return {}
        features = self.get_feature_manager().features()
        groups = self.get_feature_manager().groups()
        feature_manifests: dict[str, FeatureManifest | FeatureGroupManifest] = {}

        def _parse_feature(
            feature: BaseFeature, feature_manifest_dict: dict
        ) -> FeatureManifest:
            feature_config_dict = feature_manifest_dict.pop("config", None)
            feature_manifest = FeatureManifest.model_validate(feature_manifest_dict)
            feature_config_type = feature.config_type()
            if feature_config_type:
                if feature_config_dict:
                    feature_manifest.config = feature_config_type.model_validate(
                        feature_config_dict
                    )
                else:
                    feature_manifest.config = feature_config_type()
            return feature_manifest

        for name, feature_or_group_manifest_dict in feature_manifest_data.items():
            feature = features.get(name)
            group = groups.get(name)
            if not feature and not group:
                LOG.warning(f"Feature {name} not found in feature manager.")
                continue
            if feature and feature_or_group_manifest_dict:
                feature_manifests[name] = _parse_feature(
                    feature, feature_or_group_manifest_dict
                )
            elif group and feature_or_group_manifest_dict:
                group_manifest = FeatureGroupManifest(root={})
                for (
                    name,
                    feature_manifest_dict,
                ) in feature_or_group_manifest_dict.items():
                    feature = features.get(group.name + "." + name)
                    if not feature:
                        LOG.warning(f"Feature {name} not found in group {group.name}.")
                        continue
                    if not feature_manifest_dict:
                        continue
                    group_manifest.root[name] = _parse_feature(
                        feature, feature_manifest_dict
                    )
                if group_manifest.root:
                    feature_manifests[group.name] = group_manifest

        return feature_manifests

    def parse_manifest(self, manifest_data: dict) -> Manifest:
        """Parse manifest data."""
        features = manifest_data.pop("features", {})
        manifest = Manifest.model_validate(manifest_data)
        if features:
            manifest.features = self.parse_feature_manifest(features)
        return manifest

    def get_manifest(self, manifest_file: pathlib.Path | None = None) -> Manifest:
        """Return the manifest for the deployment."""
        if self._manifest is not None:
            return self._manifest

        feature_manager = self.get_feature_manager()
        manifest = Manifest(features=feature_manager.get_all_feature_manifests())

        override_manifest = None
        if manifest_file is not None:
            manifest_dict = yaml.safe_load(manifest_file.read_text("utf-8"))
            override_manifest = self.parse_manifest(manifest_dict)
            LOG.debug("Manifest loaded from file.")
        else:
            try:
                client = self.get_client()
                override_manifest = self.parse_manifest(
                    yaml.safe_load(client.cluster.get_latest_manifest()["data"])
                )
                LOG.debug("Manifest loaded from clusterd.")
            except ClusterServiceUnavailableException:
                LOG.debug(
                    "Failed to get manifest from clusterd, might not be bootstrapped,"
                    " consider default manifest."
                )
            except ConfigItemNotFoundException:
                LOG.debug(
                    "No manifest found in clusterd, consider default"
                    " manifest from database."
                )
            except ValueError:
                LOG.debug(
                    "Failed to get clusterd client, might no be bootstrapped,"
                    " consider empty manifest from database."
                )
            if override_manifest is None:
                # Only get manifest from embedded if manifest not present in clusterd
                snap = Snap()
                risk = infer_risk(snap)
                if risk != RiskLevel.STABLE:
                    version = infer_version(snap)
                    manifest_file = embedded_manifest_path(snap, version, risk)
                    override_manifest = self.parse_manifest(
                        yaml.safe_load(manifest_file.read_text())
                    )
                    LOG.debug("Manifest loaded from embedded manifest.")

        if override_manifest is not None:
            override_manifest.validate_against_default(manifest)
            manifest = manifest.merge(override_manifest)

        self._manifest = manifest
        return manifest

    def _get_juju_clusterd_env(self) -> dict:
        env = {}
        if self.juju_controller and self.juju_account:
            env.update(
                {
                    "JUJU_USERNAME": self.juju_account.user,
                    "JUJU_PASSWORD": self.juju_account.password,
                    "JUJU_CONTROLLER_ADDRESSES": ",".join(
                        self.juju_controller.api_endpoints
                    ),
                    "JUJU_CA_CERT": self.juju_controller.ca_cert,
                }
            )
        if self.clusterd_certpair:
            env.update(
                {
                    "TF_HTTP_CLIENT_CERTIFICATE_PEM": self.clusterd_certpair.certificate,  # noqa E501
                    "TF_HTTP_CLIENT_PRIVATE_KEY_PEM": self.clusterd_certpair.private_key,  # noqa E501
                }
            )
        return env

    def _load_tfhelpers(self):
        feature_manager = self.get_feature_manager()
        tfvar_map = copy.deepcopy(MANIFEST_ATTRIBUTES_TFVAR_MAP)
        tfvar_map_feature = feature_manager.get_all_feature_manifest_tfvar_map()
        tfvar_map = sunbeam_utils.merge_dict(tfvar_map, tfvar_map_feature)

        manifest = self.get_manifest()
        if not manifest.core.software.terraform:
            raise MissingTerraformInfoException("Manifest is missing terraform plans.")
        terraform_plans = manifest.core.software.terraform.copy()
        for _, feature in manifest.get_features():
            if not feature.software.terraform:
                continue
            terraform_plans.update(feature.software.terraform.copy())

        env = {}
        env.update(self._get_juju_clusterd_env())
        env.update(self.get_proxy_settings())

        for tfplan, tf_manifest in terraform_plans.items():
            tfplan_dir = TERRAFORM_DIR_NAMES.get(tfplan, tfplan)
            src = tf_manifest.source
            dst = self.plans_directory / tfplan_dir
            LOG.debug(f"Updating {dst} from {src}...")
            shutil.copytree(src, dst, dirs_exist_ok=True)

            self._tfhelpers[tfplan] = TerraformHelper(
                path=dst,
                plan=tfplan,
                tfvar_map=tfvar_map.get(tfplan, {}),
                backend="http",
                env=env,
                clusterd_address=self.get_clusterd_http_address(),
            )

    @property
    def plans_directory(self) -> pathlib.Path:
        """Return plans directory."""
        # TODO(gboutry): Remove snap instanciation
        snap = Snap()
        return snap.paths.user_common / "etc" / self.name

    def reload_tfhelpers(self):
        """Reload tfhelpers to update juju environment variables."""
        env = self._get_juju_clusterd_env()
        for tfplan, tfhelper in self._tfhelpers.items():
            tfhelper.reload_env(env)

    def get_tfhelper(self, tfplan: str) -> TerraformHelper:
        """Get an instance of TerraformHelper for the given tfplan.

        This method will load every tfhelper on first use.
        """
        if len(self._tfhelpers) == 0:
            self._load_tfhelpers()

        if tfhelper := self._tfhelpers.get(tfplan):
            return tfhelper

        raise ValueError(f"{tfplan} not found in tfhelpers")

    def get_space(self, network: Networks) -> str:
        """Get space associated to network."""
        return NotImplemented

    @property
    def internal_ip_pool(self):
        """Name of the internal IP pool."""
        raise NotImplementedError

    @property
    def public_ip_pool(self):
        """Name of the public IP pool."""
        raise NotImplementedError
