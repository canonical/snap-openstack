# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import pkgutil

import click

import sunbeam.storage_backends

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("storage", context_settings=CONTEXT_SETTINGS)
def storage():
    """Manage Cinder back-ends (default Ceph, plus Hitachi)."""
    pass


# Discover and register all storage backends
for finder, name, ispkg in pkgutil.iter_modules(sunbeam.storage_backends.__path__):
    if name == "base":
        continue
    mod = importlib.import_module(f"sunbeam.storage_backends.{name}")
    backend_class = getattr(mod, f"{name.capitalize()}Backend")
    storage.add_command(backend_class.commands())
