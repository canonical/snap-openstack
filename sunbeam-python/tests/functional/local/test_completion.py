# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Functional tests for Sunbeam tab completion.

Tests Sunbeam completion system with both the pre-built snap cache and the live fallback
from Click that is cached at runtime.

These functional tests require the OpenStack snap to be installed.
"""

import os
import subprocess
import time

import pytest

SNAP_NAME = "openstack"
SUNBEAM_APP = f"{SNAP_NAME}.sunbeam"


@pytest.fixture(scope="session", autouse=True)
def ensure_openstack_snap_installed_fixture():
    """Ensure the OpenStack snap is installed for these functional tests."""
    try:
        from .utils import ensure_openstack_snap_installed

        ensure_openstack_snap_installed()
    except ImportError:
        # Fallback: if this test is run as a standalone file
        try:
            subprocess.run(
                ["snap", "list", SNAP_NAME], capture_output=True, text=True, check=True
            )
        except subprocess.CalledProcessError:
            pytest.skip(f"Sunbeam snap '{SNAP_NAME}' is not installed")


# Snap completion cache generated at build time
SNAP_CACHE_ENTRIES = {
    "sunbeam",
    "sunbeam_cluster",
    "sunbeam_cluster_refresh",
    "sunbeam_configure",
    "sunbeam_deployment",
    "sunbeam_identity",
    "sunbeam_identity_provider",
    "sunbeam_juju",
    "sunbeam_manifest",
    "sunbeam_plans",
    "sunbeam_proxy",
    "sunbeam_utils",
}

# Sunbeam top-level commands
EXPECTED_TOP_LEVEL_COMMANDS = {
    "cloud-config",
    "cluster",
    "configure",
    "dashboard-url",
    "deployment",
    "disable",
    "enable",
    "identity",
    "juju",
    "launch",
    "list-feature-gates",
    "list-features",
    "manifest",
    "openrc",
    "plans",
    "prepare-node-script",
    "proxy",
    "storage",
    "utils",
}

# Sunbeam commands with pre-built cache with static subcommands
CACHED_SUBCOMMANDS = {
    "cluster": {
        "add",
        "add-secondary-region-node",
        "bootstrap",
        "join",
        "list",
        "refresh",
        "remove",
        "resize",
    },
    "proxy": {"clear", "set", "show"},
    "manifest": {"generate", "list", "show"},
}

# Performance thresholds (seconds)
STATIC_CACHE_THRESHOLD = 0.5
RUNTIME_CACHE_THRESHOLD = 1.5
LIVE_THRESHOLD = 5.0


def get_snap_env(var):
    """Get a snap environment variable value."""
    result = subprocess.run(
        ["snap", "run", "--shell", SUNBEAM_APP, "-c", f"echo ${var}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def run_completion(command_words, comp_cword=None):
    """Invoke snap completion and return parsed results.

    :param command_words: list of command words to complete, e.g. ["sunbeam", "cluster"]
    :param comp_cword: index of the word being completed (default: last word index)

    :return: tuple of (completions list, elapsed seconds)
    """
    if comp_cword is None:
        comp_cword = len(command_words)

    # Build the completion line (trailing space = completing next word)
    comp_line = " ".join(command_words) + " "
    comp_point = len(comp_line)

    # snap run --command=complete <app> <COMP_POINT> <COMP_POINT>
    # <COMP_CWORD> <NWORDS> <COMP_WORDBREAKS> "<COMP_LINE>" <app> [words...]
    nwords = len(command_words)
    special_chars = """"'><=;|&(:"""
    cmd = [
        "snap",
        "run",
        "--command=complete",
        SUNBEAM_APP,
        str(comp_point),
        str(comp_point),
        str(comp_cword),
        str(nwords),
        special_chars,
        comp_line,
        SUNBEAM_APP,
    ] + command_words[1:]

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    elapsed = time.monotonic() - start

    # Output format from etelpmoc.sh: first line may be "nosort",
    # followed by blank lines, then completion values (plain names).
    completions = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line and line != "nosort":
            completions.append(line)

    return completions, elapsed


def clear_runtime_cache():
    """Remove the runtime completion cache file."""
    snap_user_data = get_snap_env("SNAP_USER_DATA")
    cache_file = os.path.join(snap_user_data, ".sunbeam-completion-cache")
    if os.path.isfile(cache_file):
        os.remove(cache_file)


class TestSnapCache:
    """Tests for pre-built snap cache (read-only, built at snap build time)."""

    def test_snap_cache_exists(self):
        """Verify the .sunbeam-completion-cache file exists in the snap."""
        snap_dir = get_snap_env("SNAP")
        cache_path = os.path.join(
            snap_dir,
            "usr",
            "share",
            "bash-completion",
            "completions",
            ".sunbeam-completion-cache",
        )
        result = subprocess.run(
            [
                "snap",
                "run",
                "--shell",
                SUNBEAM_APP,
                "-c",
                f"test -f {cache_path}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"Snap cache file not found: {cache_path}"

    def test_snap_cache_entries(self):
        """Verify expected cache section headers are present in the snap."""
        snap_dir = get_snap_env("SNAP")
        cache_path = os.path.join(
            snap_dir,
            "usr",
            "share",
            "bash-completion",
            "completions",
            ".sunbeam-completion-cache",
        )
        result = subprocess.run(
            [
                "snap",
                "run",
                "--shell",
                SUNBEAM_APP,
                "-c",
                f"grep '^## ' {cache_path}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"grep failed (rc={result.returncode}) for cache: {cache_path}"
        )
        entries = {
            line.strip().removeprefix("## ")
            for line in result.stdout.strip().splitlines()
            if line.strip()
        }
        assert entries == SNAP_CACHE_ENTRIES

    def test_top_level_completions(self):
        """Verify sunbeam top-level completions return expected commands."""
        completions, elapsed = run_completion(["sunbeam"])
        assert elapsed < STATIC_CACHE_THRESHOLD, (
            f"Top-level completion too slow: {elapsed:.3f}s "
            f"(threshold: {STATIC_CACHE_THRESHOLD}s)"
        )
        completions_set = set(completions)
        missing = EXPECTED_TOP_LEVEL_COMMANDS - completions_set
        assert not missing, f"Missing top-level commands: {missing}"

    @pytest.mark.parametrize(
        "parent,expected_subs",
        list(CACHED_SUBCOMMANDS.items()),
        ids=list(CACHED_SUBCOMMANDS.keys()),
    )
    def test_cached_subcommand_completions(self, parent, expected_subs):
        """Verify cached subcommand completions are fast and correct."""
        completions, elapsed = run_completion(["sunbeam", parent])
        assert elapsed < STATIC_CACHE_THRESHOLD, (
            f"Cached completion for '{parent}' too slow: "
            f"{elapsed:.3f}s (threshold: {STATIC_CACHE_THRESHOLD}s)"
        )
        completions_set = set(completions)
        missing = expected_subs - completions_set
        assert not missing, f"Missing subcommands for '{parent}': {missing}"

    def test_static_cache_average_performance(self):
        """Report average performance across all static cache lookups."""
        timings = []
        # Top-level
        _, elapsed = run_completion(["sunbeam"])
        timings.append(("sunbeam", elapsed))
        # Subcommands
        for parent in CACHED_SUBCOMMANDS:
            _, elapsed = run_completion(["sunbeam", parent])
            timings.append((f"sunbeam {parent}", elapsed))

        avg = sum(t for _, t in timings) / len(timings)
        report = ", ".join(f"{name}: {t:.3f}s" for name, t in timings)
        print(f"\nStatic cache avg: {avg:.3f}s [{report}]")
        assert avg < STATIC_CACHE_THRESHOLD, (
            f"Static cache average too slow: {avg:.3f}s "
            f"(threshold: {STATIC_CACHE_THRESHOLD}s)"
        )


class TestLiveFallbackAndRuntimeCache:
    """Tests for live fallback completion and runtime caching."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Clear runtime cache before each test."""
        clear_runtime_cache()

    def test_live_fallback_returns_results(self):
        """Verify live fallback works for commands not in snap cache."""
        # 'enable' has dynamic subcommands, not in snap cache
        completions, elapsed = run_completion(["sunbeam", "enable"])
        assert elapsed < LIVE_THRESHOLD, (
            f"Live fallback too slow: {elapsed:.3f}s (threshold: {LIVE_THRESHOLD}s)"
        )
        assert len(completions) > 0, "Live fallback returned no completions"

    def test_live_average_performance(self):
        """Report average performance for live (uncached) completion lookups."""
        timings = []
        commands = ["enable", "disable"]
        for cmd in commands:
            clear_runtime_cache()
            _, elapsed = run_completion(["sunbeam", cmd])
            timings.append((f"sunbeam {cmd}", elapsed))

        avg = sum(t for _, t in timings) / len(timings)
        report = ", ".join(f"{name}: {t:.3f}s" for name, t in timings)
        print(f"\nLive fallback avg: {avg:.3f}s [{report}]")
        assert avg < LIVE_THRESHOLD, (
            f"Live fallback average too slow: {avg:.3f}s (threshold: {LIVE_THRESHOLD}s)"
        )

    def test_runtime_cache_created(self):
        """Verify runtime cache entry is created after live fallback."""
        run_completion(["sunbeam", "enable"])

        snap_user_data = get_snap_env("SNAP_USER_DATA")
        cache_file = os.path.join(snap_user_data, ".sunbeam-completion-cache")
        result = subprocess.run(
            [
                "snap",
                "run",
                "--shell",
                SUNBEAM_APP,
                "-c",
                f"grep -q '^## sunbeam_enable$' {cache_file}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"Runtime cache entry 'sunbeam_enable' not found in: {cache_file}"
        )

    def test_runtime_cached_faster_than_live(self):
        """Verify second call (runtime cached) is faster than first (live)."""
        # First call: live fallback
        _, elapsed_live = run_completion(["sunbeam", "enable"])

        # Second call: should hit runtime cache
        _, elapsed_cached = run_completion(["sunbeam", "enable"])

        assert elapsed_cached < RUNTIME_CACHE_THRESHOLD, (
            f"Runtime cached completion too slow: "
            f"{elapsed_cached:.3f}s (threshold: {RUNTIME_CACHE_THRESHOLD}s)"
        )
        assert elapsed_cached < elapsed_live, (
            f"Cached ({elapsed_cached:.3f}s) not faster than live ({elapsed_live:.3f}s)"
        )

    def test_runtime_cache_average_performance(self):
        """Report average performance for runtime cache lookups."""
        timings = []
        commands = ["enable", "disable"]
        for cmd in commands:
            # First call populates runtime cache
            run_completion(["sunbeam", cmd])
            # Second call reads from runtime cache
            _, elapsed = run_completion(["sunbeam", cmd])
            timings.append((f"sunbeam {cmd}", elapsed))

        avg = sum(t for _, t in timings) / len(timings)
        report = ", ".join(f"{name}: {t:.3f}s" for name, t in timings)
        print(f"\nRuntime cache avg: {avg:.3f}s [{report}]")
        assert avg < RUNTIME_CACHE_THRESHOLD, (
            f"Runtime cache average too slow: {avg:.3f}s "
            f"(threshold: {RUNTIME_CACHE_THRESHOLD}s)"
        )

    def test_runtime_cache_consistent_results(self):
        """Verify cached results match live results."""
        completions_live, _ = run_completion(["sunbeam", "enable"])
        completions_cached, _ = run_completion(["sunbeam", "enable"])

        assert set(completions_live) == set(completions_cached), (
            f"Cached results differ from live: "
            f"live={sorted(completions_live)}, cached={sorted(completions_cached)}"
        )


class TestCompleterScript:
    """Tests for the completer script itself."""

    def test_completer_script_installed(self):
        """Verify the sunbeam completer script is in the snap."""
        snap_dir = get_snap_env("SNAP")
        completer = os.path.join(
            snap_dir,
            "usr",
            "share",
            "bash-completion",
            "completions",
            "sunbeam-completion",
        )
        result = subprocess.run(
            [
                "snap",
                "run",
                "--shell",
                SUNBEAM_APP,
                "-c",
                f"test -f {completer}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"Completer script not found: {completer}"

    def test_completer_registers_both_commands(self):
        """Verify completer registers both 'sunbeam' and 'openstack.sunbeam'."""
        snap_dir = get_snap_env("SNAP")
        completer = os.path.join(
            snap_dir,
            "usr",
            "share",
            "bash-completion",
            "completions",
            "sunbeam-completion",
        )
        result = subprocess.run(
            [
                "snap",
                "run",
                "--shell",
                SUNBEAM_APP,
                "-c",
                f"cat {completer}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"Failed to read completer script (rc={result.returncode}): {completer}"
        )
        content = result.stdout
        assert "complete" in content and "sunbeam" in content
        assert "openstack.sunbeam" in content
