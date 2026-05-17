# CLAUDE.md — OpenStack Snap Migration Guide (Gazpacho 2026.1 / Ubuntu 26.04 / Python 3.14)

## Project Context
You are an expert software engineer.
This is a snap package for OpenStack components, being migrated to:
- **OpenStack release:** 2026.1 (Gazpacho)
- **Ubuntu base:** 26.04 (core26 snap base)
- **Python:** 3.14

## Key Files

- `pyproject.toml` — package metadata, dependencies, entry points
- `snap/snapcraft.yaml` — snap build definition (base, parts, apps, layouts)
- `tox.ini` — test runner config (uses `uv` for unit tests)
- `openstack_hypervisor/` — main Python package
- `templates/` — Jinja2 config templates for OpenStack services
- `tests/unit/` — unit tests (pytest)

## Migration Checklist

### pyproject.toml
- Set `version = "2026.1"`
- Set `requires-python = "~=3.14.0"`
- Update classifiers to include `Programming Language :: Python :: 3.14`
- Review all dependencies for Python 3.14 compatibility

### snap/snapcraft.yaml
- Set `base: core26` and `build-base: devel` (until core26 is stable)
- Update all `PYTHONPATH` references from `python3.XX` → `python3.14`
- Update any hardcoded Python paths in parts, apps, and environment blocks
- Bump OpenStack component versions to Gazpacho (2026.1) in source-branch/source-tag fields
- Review channel for stage-snaps (e.g. `latest/edge` during development)

### Python 3.14 Compatibility Issues

#### `os.memfd_create` not available in frozen `os` module
- **Symptom:** `AttributeError: <module 'os' (frozen)> does not have the attribute 'memfd_create'`
- **Cause:** In CPython 3.14, the `os` module is frozen and `os.memfd_create` may not be present in all builds (especially CI runners). `unittest.mock.patch` raises `AttributeError` when it tries to look up an attribute that doesn't exist.
- **Fix:** Add `create=True` to `@patch()` decorators for platform-specific `os` functions:
  ```python
  # Before (breaks on 3.14):
  @patch("mymodule.os.memfd_create", return_value=10)

  # After (works everywhere):
  @patch("mymodule.os.memfd_create", create=True, return_value=10)
  ```
- **General rule:** Any `@patch` targeting a platform-specific or optional attribute in a frozen stdlib module needs `create=True`.

#### Other common Python 3.14 changes to watch for
- Deprecated functions removed from `collections`, `typing`, `importlib`, etc.
- `ctypes.Structure` with `_pack_` now warns about `_layout_` (see `pyroute2` warnings) — these are upstream dependency issues, not things to fix in the snap code.
- XML ElementTree `.find()` truth-value testing is deprecated — use `elem is not None` or `len(elem)` instead.

### Testing
- Tests use `uv` via tox: `tox -e unit`
- The `uv` lock file should be regenerated after dependency changes: `tox -e lock`
- Use `--frozen --isolated --extra=dev` flags (already configured in `tox.ini`)

## Common Patterns in This Codebase

- Services are defined in `services.py` with a `run(snap)` method pattern
- Configuration is rendered from Jinja2 templates in `templates/`
- Snap hooks (configure, install) are in `hooks.py`
- CLI uses Click, defined under `cli/`
- Tests use pytest with `unittest.mock` patching; fixtures in `conftest.py`

## Tips

- When patching stdlib functions that are platform-specific (e.g., Linux-only syscalls like `memfd_create`, `copy_file_range`, `sendfile`), always use `create=True` in `@patch`.
- If CI fails with `AttributeError: <module 'X' (frozen)> does not have the attribute 'Y'`, the frozen module in 3.14 is likely missing that attribute — use `create=True`.
- The snap uses `setpriv` to drop privileges for services — don't remove `--reuid`/`--regid` from subprocess calls.
- `snapcraft.yaml` is large (~900 lines); search for the specific part name when editing.
