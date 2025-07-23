# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import click


class StorageBackendBase:
    name = "base"

    @classmethod
    def add_backend(cls, ctx):
        """Add a backend."""
        raise NotImplementedError

    @classmethod
    def remove_backend(cls, ctx):
        """Remove a backend."""
        raise NotImplementedError

    @classmethod
    def list_backends(cls, ctx):
        """List backends."""
        raise NotImplementedError

    @classmethod
    def commands(cls):
        """Commands to add, del and list."""

        @click.group(cls.name)
        def backend_group():
            pass

        @backend_group.command("add")
        def add():
            cls.add_backend()

        @backend_group.command("del")
        def delete():
            cls.remove_backend()

        @backend_group.command("list")
        def list_():
            cls.list_backends()

        return backend_group
