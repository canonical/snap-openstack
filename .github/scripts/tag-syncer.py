# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "launchpadlib>=2.1.0",
# ]
# ///

import logging
import sys
import subprocess

from launchpadlib.launchpad import Launchpad


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def is_build_ready_for_tagging(build) -> bool:
    """Check if a build is ready for tagging."""
    if build.buildstate != "Successfully built":
        logger.warning(
            "Skipping %s: build state is '%s', not 'Successfully built'",
            build.self_link,
            build.buildstate,
        )
        return False

    if build.store_upload_status != "Uploaded":
        logger.warning(
            "Skipping %s: upload status is '%s', not 'Uploaded'",
            build.self_link,
            build.store_upload_status,
        )
        return False

    if build.revision_id is None or build.store_upload_revision is None:
        logger.warning(
            "Skipping %s: missing commit (%s) or revision (%s)",
            build.self_link,
            build.revision_id,
            build.store_upload_revision,
        )
        return False

    # Check if tag already exists
    rev = build.store_upload_revision
    tag_name = f"rev{rev}"
    if tag_exists(tag_name):
        logger.info(
            "Skipping %s: tag '%s' already exists",
            build.self_link,
            tag_name,
        )
        return False

    return True


def tag_exists(tag_name: str) -> bool:
    """Check if a git tag already exists.

    Args:
        tag_name: The name of the tag to check

    Returns:
        bool: True if tag exists, False otherwise
    """
    try:
        subprocess.run(
            ["git", "rev-parse", f"refs/tags/{tag_name}"],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def create_git_tag(build) -> bool:
    """Create a git tag for the given build.

    Returns:
        bool: True if tag was created successfully, False otherwise.
    """
    commit = build.revision_id
    rev = build.store_upload_revision
    arch = build.arch_tag
    build_link = build.web_link
    tag_name = f"rev{rev}"
    tag_message = f"Rev {rev}, built at: {build_link}\nArchitecture: {arch}"

    logger.info(
        "Creating tag '%s' for commit %s (arch: %s)", tag_name, commit[:8], arch
    )

    try:
        subprocess.run(
            [
                "git",
                "tag",
                tag_name,
                commit,
                "-m",
                tag_message,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(
            "Successfully created tag '%s' for build %s", tag_name, build.self_link
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            "Failed to create tag '%s' for build %s: %s", tag_name, build.self_link, e
        )
        if e.stderr:
            logger.error("Git error output: %s", e.stderr.strip())
        return False


def process_build(build) -> bool:
    """Process a single build and create a tag if appropriate.

    Returns:
        bool: True if build was processed successfully (tagged or skipped), False on error.
    """
    if not is_build_ready_for_tagging(build):
        return True

    return create_git_tag(build)


def load_snap_recipes(lp, snap_recipes: list[str]) -> list:
    """Load snap recipes from Launchpad.

    Args:
        lp: Launchpad instance
        snap_recipes: List of snap recipe URLs

    Returns:
        List of loaded snap recipe objects
    """
    snaps = []
    for snap_recipe in snap_recipes:
        try:
            logger.info("Loading snap recipe: %s", snap_recipe)
            snap = lp.load(snap_recipe)
            snaps.append(snap)
            logger.info("Successfully loaded snap recipe: %s", snap.name)
        except Exception as e:
            # Handle 404 errors specifically for missing snap recipes
            # Check if this is an HTTP error with a 404 status
            error_message = str(e)
            if "404" in error_message or "Not Found" in error_message:
                logger.error("Snap recipe not found: %s", snap_recipe)
                continue
            # Re-raise other exceptions
            logger.error("Failed to load snap recipe %s: %s", snap_recipe, e)
            raise

    return snaps


def collect_builds_from_snaps(snaps: list, max_builds_per_snap: int = 10) -> list:
    """Collect builds from snap recipes.

    Args:
        snaps: List of snap recipe objects
        max_builds_per_snap: Maximum number of builds to fetch per snap

    Returns:
        List of builds sorted by build ID
    """
    builds = []
    for snap in snaps:
        logger.info("Collecting builds from snap: %s", snap.name)
        snap_builds = snap.completed_builds[:max_builds_per_snap]
        builds.extend(snap_builds)
        logger.info("Collected %d builds from %s", len(snap_builds), snap.name)

    # Sort builds by build ID (extracted from self_link)
    # Sorting just to ensure tags will be created chronologically
    builds = sorted(builds, key=lambda build: int(build.self_link.rsplit("/", 1)[-1]))
    logger.info("Total builds collected: %d", len(builds))

    return builds


def main(*snap_recipes: str) -> None:
    """Main function to process snap recipes and create git tags."""
    if not snap_recipes:
        logger.error("No snap recipes provided")
        sys.exit(1)

    logger.info("Starting tag syncer for %d snap recipe(s)", len(snap_recipes))
    logger.info("Snap recipes: %s", list(snap_recipes))

    logger.info("Authenticating with Launchpad")
    try:
        lp = Launchpad.login_anonymously(
            "snap-release-tagger", "production", version="devel"
        )
        logger.info("Successfully authenticated with Launchpad")
    except Exception as e:
        logger.error("Failed to authenticate with Launchpad: %s", e)
        sys.exit(1)

    try:
        snaps = load_snap_recipes(lp, list(snap_recipes))
        if not snaps:
            logger.error("No snap recipes were successfully loaded")
            sys.exit(1)
    except Exception as e:
        logger.error("Failed to load snap recipes: %s", e)
        sys.exit(1)

    builds = collect_builds_from_snaps(snaps)
    if not builds:
        logger.warning("No builds found to process")
        return

    logger.info("Processing builds...")
    successful_tags = 0
    failed_tags = 0

    for build in builds:
        try:
            if process_build(build):
                successful_tags += 1
            else:
                failed_tags += 1
        except Exception as e:
            logger.error("Unexpected error processing build %s: %s", build.self_link, e)
            failed_tags += 1

    total_processed = successful_tags + failed_tags
    logger.info(
        "Processing complete: %d successful, %d failed out of %d builds",
        successful_tags,
        failed_tags,
        total_processed,
    )

    if failed_tags > 0:
        logger.warning("%d builds failed to process", failed_tags)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: tag-syncer.py <snap-recipes...>")
        logger.error(
            "Example: tag-syncer.py ~openstack-snappers/snap-openstack/+snap/openstack-caracal-candidate ~openstack-snappers/+snap/openstack-caracal-edge"
        )
        sys.exit(1)

    main(*sys.argv[1:])
