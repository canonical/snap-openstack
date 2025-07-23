# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import re

import click

from sunbeam.core.juju import JujuHelper

from .base import StorageBackendBase


class HitachiBackend(StorageBackendBase):
    name = "hitachi"

    @staticmethod
    def _validate_ip_or_fqdn(value: str) -> str:
        # Try to validate as an IP address
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            pass  # Not an IP, check for FQDN next

        # Regex to validate FQDN
        fqdn_regex = re.compile(
            r"^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}\.?$"
        )
        if fqdn_regex.match(value):
            return value

        raise click.BadParameter(f"{value} is not a valid IP address or FQDN.")

    @classmethod
    def add_backend(cls, ctx: click.Context):
        """Add a new Hitachi storage backend."""
        name = click.prompt("Backend name", default="hitachi-vsp")
        serial = click.prompt("Array serial")
        pools = click.prompt("Pools (comma separated)")
        proto = click.prompt(
            "Protocol", type=click.Choice(["FC", "iSCSI"]), default="FC"
        )
        san_ip = click.prompt("Management IP/FQDN", value_proc=cls._validate_ip_or_fqdn)
        user = click.prompt("SAN Username", default="maintenance")
        passwd = click.prompt("SAN Password", hide_input=True)

        cfg = {
            "volume-backend-name": name,
            "hitachi-storage-id": serial,
            "hitachi-pools": pools,
            "san-ip": san_ip,
            "san-login": user,
            "san-password": passwd,
            "protocol": proto.lower(),
        }

        deployment = ctx.obj
        jhelper = JujuHelper(deployment.juju_controller)
        # TODO: verify that the juju helper is authenticated and the model is set

        model = (
            deployment.openstack_machines_model
            if deployment.openstack_machines_model.startswith("admin/")
            else f"admin/{deployment.openstack_machines_model}"
        )
        click.echo(f"Hitachi backend '{name}' is now deploying.")
        try:
            jhelper.deploy(
                "cinder-volume-hitachi", "cinder-volume-hitachi", model, config=cfg
            )
        except Exception as e:
            click.echo(f"Failed to deploy Hitachi backend '{name}': {e}")
            return
        click.echo(f"Hitachi backend '{name}' is now integrating.")
        try:
            jhelper.integrate(
                model, "cinder-volume-hitachi", "cinder-volume", "cinder-volume"
            )
        except Exception as e:
            click.echo(f"Failed to integrate Hitachi backend '{name}': {e}")
            return
        click.echo(f"Hitachi backend '{name}' is now ready.")
        click.echo(
            "You can now create volumes using the 'cinder create' command with "
            f"the backend '{name}'."
        )

    @classmethod
    def remove_backend(cls, ctx: click.Context):
        """Remove a Hitachi storage backend."""
        raise NotImplementedError

    @classmethod
    def list_backends(cls, ctx: click.Context):
        """List all deployed Cinder backends (including Hitachi)."""
        import subprocess

        import yaml

        deployment = ctx.obj
        # the model is created with an admin/ before.
        # In some Juju deployment scenarios, models are prefixed with
        # "admin/" to indicate the administrative namespace.
        # TODO: find a better way to handle this
        model = (
            deployment.openstack_machines_model
            if deployment.openstack_machines_model.startswith("admin/")
            else f"admin/{deployment.openstack_machines_model}"
        )
        try:
            result = subprocess.run(
                ["juju", "status", "--format=yaml", "--model", model],
                capture_output=True,
                text=True,
                check=True,  # This will raise CalledProcessError if the command fails
                timeout=30,  # Timeout in seconds
            )
            data = yaml.safe_load(result.stdout)
            if not data:
                click.echo(
                    "No data returned from Juju status or invalid YAML.", err=True
                )
                return
            apps = data.get("applications", {})
            cinder_backends = [n for n in apps if n.startswith("cinder-volume")]
            if not cinder_backends:
                click.echo("No back-ends deployed (Ceph is the implicit default).")
                return
            for app in cinder_backends:
                app_info = apps.get(app, {})
                status = app_info.get("status", "unknown")
                config = app_info.get("charm", "unknown")
                click.echo(f"Backend: {app}")
                click.echo(f"  Status: {status}")
                click.echo(f"  Charm: {config}")
                if "config" in app_info:
                    click.echo(f"  Config: {app_info['config']}")
        except subprocess.CalledProcessError as e:
            click.echo(f"Failed to get Juju status: {e}", err=True)
        except Exception as e:
            click.echo(f"An error occurred: {e}", err=True)

    @classmethod
    def commands(cls):
        """Hitachi backend commands, add, remove, list."""

        @click.group(cls.name, help="Manage Hitachi Cinder backends.")
        def backend_group():
            pass

        @backend_group.command("add", help="Add a new Hitachi backend.")
        @click.pass_context
        def add(ctx: click.Context):
            cls.add_backend(ctx)

        @backend_group.command("remove", help="Remove a Hitachi backend.")
        @click.pass_context
        def remove(ctx: click.Context):
            cls.remove_backend(ctx)

        @backend_group.command("list", help="List all Cinder backends.")
        @click.pass_context
        def list_(ctx: click.Context):
            cls.list_backends(ctx)

        return backend_group
