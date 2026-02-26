# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import logging
import pathlib
import typing

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

import sunbeam.features
from sunbeam import utils
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
    SunbeamException,
    infer_risk,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import FeatureGroupManifest, FeatureManifest
from sunbeam.feature_gates import log_gated_feature
from sunbeam.features.interface.v1.base import (
    BaseFeature,
    BaseFeatureGroup,
    EnableDisableFeature,
)
from sunbeam.features.interface.v1.base import (
    features as all_features,
)
from sunbeam.features.interface.v1.base import (
    groups as all_groups,
)
from sunbeam.versions import VarMap

LOG = logging.getLogger(__name__)
console = Console()

_FEATURES: dict[str, BaseFeature] = {}


@click.command("list-features")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def list_features(ctx: click.Context, format: str) -> None:
    """List all features and show whether each is enabled or not."""
    deployment: Deployment = ctx.obj
    try:
        client = deployment.get_client()
        feature_manager = deployment.get_feature_manager()
    except ClusterServiceUnavailableException as e:
        raise click.ClickException(
            "Cannot list features: cluster service is not available. "
            "Ensure the node is part of a bootstrapped cluster."
        ) from e

    feature_data: dict[str, dict[str, bool]] = {}
    for name, feature in feature_manager.features().items():
        if not isinstance(feature, EnableDisableFeature):
            continue
        try:
            enabled = feature.is_enabled(client)
        except Exception as e:
            LOG.debug("Failed to get status for feature %r: %r", name, e)
            enabled = False

        # Check if feature is experimental (not generally available)
        experimental = not getattr(feature, "generally_available", True)

        feature_data[name] = {
            "enabled": enabled,
            "experimental": experimental,
        }

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Feature", justify="left")
        table.add_column("Enabled", justify="center")
        table.add_column("Experimental", justify="center")
        for name, data in feature_data.items():
            table.add_row(
                name,
                "X" if data["enabled"] else "",
                "X" if data["experimental"] else "",
            )
        console.print(table)
    elif format == FORMAT_YAML:
        console.print(yaml.dump(feature_data))


class FeatureManager:
    """Class to expose functions to interact with features.

    Implement the functions required either by sunbeam
    cli or any other cluster operations that need to be
    triggered on all or some of the features.
    """

    _features: dict[str, BaseFeature] = _FEATURES
    _groups: dict[str, BaseFeatureGroup] = {}

    def __init__(self) -> None:
        if not self._features or not self._groups:
            groups, features = self._load_features()
            self._groups.update(groups)
            self._features.update(features)

    def _load_features(
        self,
    ) -> tuple[typing.Mapping[str, BaseFeatureGroup], typing.Mapping[str, BaseFeature]]:
        """Load all the features."""
        sunbeam_features = pathlib.Path(sunbeam.features.__file__).parent
        for path in sunbeam_features.iterdir():
            if not path.is_dir() or path.name.startswith("_"):
                continue
            if not (path / "feature.py").exists():
                continue
            importlib.import_module(".feature", "sunbeam.features." + path.name)
        groups = {}
        for name, group in all_groups().items():
            groups[name] = group()
        features = {}
        for name, feature in all_features().items():
            features[name] = feature()
        return groups, features

    def features(self) -> typing.Mapping[str, BaseFeature]:
        """Return all the features."""
        return self._features

    def groups(self) -> typing.Mapping[str, BaseFeatureGroup]:
        """Return all the feature groups."""
        return self._groups

    @classmethod
    def get_all_feature_classes(cls) -> list[type]:
        """Return a list of feature classes."""
        return list(all_features().values())

    def enabled_features(self, deployment: Deployment) -> list[EnableDisableFeature]:
        """Returns feature names that are enabled.

        :param deployment: Deployment instance.
        :returns: List of enabled features
        """
        enabled_features = []
        for name, feature in self.features().items():
            if isinstance(feature, EnableDisableFeature) and feature.is_enabled(
                deployment.get_client()
            ):
                enabled_features.append(feature)

        LOG.debug("Enabled features: %s", ",".join(f.name for f in enabled_features))
        return enabled_features

    def is_feature_enabled(self, deployment: Deployment, name: str) -> bool:
        """Returns true if feature is enabled otherwise false.

        :param deployment: Deployment instance.
        :param name: Name of the feature.
        :returns: Boolean
        """
        feature = self.features().get(name)
        if feature is None:
            raise SunbeamException(f"Feature {name} does not exist")
        if not isinstance(feature, EnableDisableFeature):
            raise SunbeamException(f"Feature {name} is not of type EnableDisable")

        return feature.is_enabled(deployment.get_client())

    def get_all_feature_manifests(
        self,
    ) -> dict[str, FeatureManifest | FeatureGroupManifest]:
        """Return a dict of all feature manifest defaults."""
        manifests: dict[str, FeatureManifest | FeatureGroupManifest] = {}
        groups: dict[str, FeatureGroupManifest] = {}
        for name, feature in self.features().items():
            if feature.group:
                display_name = name.removeprefix(f"{feature.group.name}.")
                groups.setdefault(
                    feature.group.name, FeatureGroupManifest(root={})
                ).root[display_name] = FeatureManifest(
                    software=feature.default_software_overrides()
                )
            else:
                manifests[name] = FeatureManifest(
                    software=feature.default_software_overrides()
                )
        manifests.update(groups)
        return manifests

    def get_all_feature_manifest_tfvar_map(self) -> dict[str, VarMap]:
        """Return a dict of all feature manifest attributes terraformvars map."""
        tfvar_map: dict[str, VarMap] = {}
        for feature in self.features().values():
            m_dict = feature.manifest_attributes_tfvar_map()
            utils.merge_dict(tfvar_map, m_dict)

        return tfvar_map

    def get_preseed_questions_content(self) -> list:
        """Allow every feature to add preseed questions to the preseed file."""
        content = []
        for feature in self.features().values():
            content.extend(feature.preseed_questions_content())

        return content

    def get_all_charms_in_openstack_plan(self) -> list:
        """Return all charms in openstack-plan from all features."""
        charms = []
        for feature in self.features().values():
            m_dict = feature.manifest_attributes_tfvar_map()
            charms_from_feature = list(
                m_dict.get("openstack-plan", {}).get("charms", {}).keys()
            )
            charms.extend(charms_from_feature)

        return charms

    def update_proxy_model_configs(
        self, deployment: Deployment, show_hints: bool
    ) -> None:
        """Make all features update proxy model configs."""
        for feature in self.features().values():
            feature.update_proxy_model_configs(deployment, show_hints)

    def register(self, cli: click.Group, deployment: Deployment) -> None:
        """Register the features.

        Register the features. Once registered, all the commands/groups defined by
        features will be shown as part of sunbeam cli.

        Features are checked against:
        1. Risk level availability
        2. Feature gates (via snap config feature.<feature-name>)

        :param deployment: Deployment instance.
        :param cli: Main click group for sunbeam cli.
        """
        LOG.debug("Registering features")
        installation_risk = infer_risk(Snap())
        snap = Snap()

        for group in self.groups().values():
            group.register(cli)

        for feature in self.features().values():
            # Check 1: Risk level availability
            if feature.risk_availability > installation_risk:
                LOG.debug(
                    "Not registering feature %r,"
                    " it is available at a higher risk level",
                    feature.name,
                )
                continue

            # Check 2: Feature gates - skip if feature is gated
            client = None
            if deployment:
                try:
                    client = deployment.get_client()
                except SunbeamException:
                    # Cannot get client (e.g., insufficient permissions)
                    # Check will proceed with client=None
                    pass

            if hasattr(feature, "check_gated") and feature.check_gated(
                client=client,
                snap=snap,
                # Features track their own enabled state via is_enabled() method,
                # so enabled_config_key is None. Storage backends use this to check
                # if the backend type is in the "StorageBackendsEnabled" config.
                enabled_config_key=None,
            ):
                log_gated_feature(feature.name, feature.gate_key)
                continue

            try:
                enabled = feature.is_enabled(deployment.get_client())  # type: ignore
            except AttributeError:
                LOG.debug("Feature %r is not enable / disable feature", feature.name)
                enabled = False
            except (
                SunbeamException,
                ValueError,
                ClusterServiceUnavailableException,
            ) as e:
                LOG.debug(
                    "Feature %r failed to check if it is enabled: %r", feature.name, e
                )
                enabled = False
            try:
                feature.register(cli, {"enabled": enabled})
            except (ValueError, SunbeamException) as e:
                LOG.debug("Failed to register feature: %r", str(feature))
                if "Clusterd address" in str(e) or "Insufficient permissions" in str(e):
                    LOG.debug(
                        "Sunbeam not bootstrapped. Ignoring feature registration."
                    )
                    continue
                raise e

    def resolve_feature(self, feature: str) -> BaseFeature | None:
        """Resolve a feature name to a class.

        Lookup features to find a feature with the given name.
        """
        return self.features().get(feature)

    def has_feature_version_changed(
        self, deployment: Deployment, feature: BaseFeature
    ) -> bool:
        """Check if feature version sas changed.

        Compare the feature version in the database and the newly loaded one
        from features.yaml. Return true if versions are different.

        :param feature: Feature object
        :returns: True if versions are different.
        """
        return not feature.get_feature_info(deployment.get_client()).get(
            "version", "0.0.0"
        ) == str(feature.version)

    def update_features(
        self,
        deployment: Deployment,
        upgrade_release: bool = False,
    ) -> None:
        """Call feature upgrade hooks.

        Get all the features and call the corresponding feature upgrade hooks
        if the feature is enabled and version is changed.

        :param deployment: Deployment instance.
        :param upgrade_release: Upgrade release flag.
        """
        for feature in self.enabled_features(deployment):
            if hasattr(feature, "upgrade_hook"):
                LOG.debug(f"Upgrading feature {feature.name}")
                try:
                    feature.upgrade_hook(deployment, upgrade_release=upgrade_release)
                except TypeError:
                    LOG.debug(
                        f"Feature {feature.name} does not support upgrades between "
                        "channels"
                    )
                    feature.upgrade_hook(deployment)
