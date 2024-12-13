# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import os
import typing
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ManifestItemNotFoundException,
)
from sunbeam.core.common import FORMAT_TABLE, FORMAT_YAML
from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import Manifest, SoftwareConfig

if typing.TYPE_CHECKING:
    from sunbeam.features.interface.v1.base import BaseFeature

LOG = logging.getLogger(__name__)
console = Console()


def generate_software_manifest(
    software_config: SoftwareConfig, initial_indent: int = 2
) -> str:
    space = " "
    indent = space * initial_indent
    comment = "# "

    try:
        software_dict = software_config.model_dump(by_alias=True)
        LOG.debug(f"Manifest software dict with extra fields: {software_dict}")

        # Remove terraform default sources
        manifest_terraform_dict = software_dict.get("terraform", {})
        for _, value in manifest_terraform_dict.items():
            if (source := value.get("source")) and str(source).startswith(
                "/snap/openstack"
            ):
                value["source"] = None

        software_yaml = yaml.safe_dump(software_dict, sort_keys=False)

        # TODO(hemanth): Add an option schema to print the JsonSchema for the
        # Manifest. This will be easier when moved to pydantic 2.x

        # add comment to each line
        software_lines = (
            f"{space * initial_indent}{comment}{space * 2}{line}"
            for line in software_yaml.split("\n")
            if line
        )
        software_yaml_commented = "\n".join(software_lines)
        software_content = f"{indent}{comment}software:\n{software_yaml_commented}"
        return software_content
    except Exception as e:
        LOG.debug(e)
        raise click.ClickException(f"Manifest generation failed: {str(e)}")


def _dump_feature(
    name: str, feature: "BaseFeature", manifest: Manifest, base_indent: int = 2
) -> str:
    space = " "
    indent = base_indent
    if feature.group:
        display_name = name.removeprefix(f"{feature.group.name}.")
    else:
        display_name = name
    feature_manifest_content = indent * space + display_name + ":\n"
    feature_indent = indent + 2
    feature_manifest_content += f"{feature_indent * space}# config:"
    if content := feature.preseed_questions_content():
        feature_manifest_content += "\n"
        for line in content:
            feature_manifest_content += f"{indent * space}{line}\n"
    else:
        feature_manifest_content += " null\n"
    if feature_manifest := manifest.get_feature(name):
        feature_manifest_content += generate_software_manifest(
            feature_manifest.software, feature_indent
        )
    return feature_manifest_content


@click.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def list_manifests(ctx: click.Context, format: str) -> None:
    """List manifests."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    manifests = []

    try:
        manifests = client.cluster.list_manifests()
    except ClusterServiceUnavailableException:
        click.echo("Error: Not able to connect to Cluster DB")
        return

    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("ID", justify="left")
        table.add_column("Applied Date", justify="left")
        for manifest in manifests:
            table.add_row(manifest.get("manifestid"), manifest.get("applieddate"))
        console.print(table)
    elif format == FORMAT_YAML:
        for manifest in manifests:
            manifest.pop("data")
        click.echo(yaml.dump(manifests))


@click.command()
@click.argument("id", type=str)
@click.pass_context
def show(ctx: click.Context, id: str) -> None:
    """Show Manifest data.

    Use ID 'latest' to get the last committed manifest.
    """
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    try:
        manifest = client.cluster.get_manifest(id)
        click.echo(manifest.get("data"))
    except ClusterServiceUnavailableException:
        click.echo("Error: Not able to connect to Cluster DB")
    except ManifestItemNotFoundException:
        click.echo(f"Error: No manifest exists with id {id}")


@click.command()
@click.option(
    "-f",
    "--manifest-file",
    help="Output file for manifest, defaults to $HOME/.config/openstack/manifest.yaml",
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.pass_context
def generate(
    ctx: click.Context,
    manifest_file: Path | None = None,
):
    """Generate manifest file.

    Generate manifest file with the deployed configuration.
    If the cluster is not bootstrapped, fallback to default
    configuration.
    """
    deployment: Deployment = ctx.obj

    if not manifest_file:
        home = os.environ["SNAP_REAL_HOME"]
        manifest_file = Path(home) / ".config" / "openstack" / "manifest.yaml"

    LOG.debug(f"Creating {manifest_file} parent directory if it does not exist")
    manifest_file.parent.mkdir(mode=0o775, parents=True, exist_ok=True)

    manifest = deployment.get_manifest()

    manifest_content = deployment.generate_core_config(console)
    manifest_content += "\n" + generate_software_manifest(manifest.core.software)
    fm = deployment.get_feature_manager()
    indent = 2
    manifest_content += "\nfeatures:"

    groups_content: dict[str, list[str]] = {}
    for name, feature in fm.features().items():
        if feature.group:
            groups_content.setdefault(feature.group.name, []).append(
                _dump_feature(name, feature, manifest, indent + 2)
            )
        else:
            manifest_content += "\n" + _dump_feature(name, feature, manifest, indent)

    for group, features_content in groups_content.items():
        manifest_content += f"\n  {group}:"
        for feature_content in features_content:
            manifest_content += "\n" + feature_content

    try:
        with manifest_file.open("w") as file:
            file.write("# Generated Sunbeam Deployment Manifest\n\n")
            file.write(manifest_content)
    except IOError as e:
        LOG.debug(e)
        raise click.ClickException(f"Manifest generation failed: {str(e)}")

    click.echo(f"Generated manifest is at {str(manifest_file)}")
