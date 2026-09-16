# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from functools import partial
from unittest.mock import Mock

import jubilant
import pytest
import yaml
from click.testing import CliRunner

from sunbeam.clusterd.service import (
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
    TerraformPlanLockConflictException,
)
from sunbeam.commands.manifest import generate
from sunbeam.core.charm_channel import CharmChannelError
from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import ApplicationNotFoundException, JujuException
from sunbeam.core.k8s_track import (
    K8S_TRACK_CONFIG_KEY,
    K8STrackGuard,
)
from sunbeam.core.manifest import AddManifestStep, Manifest
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.steps.k8s_upgrade import K8SCharmUpgradeStep
from sunbeam.versions import DEPLOY_K8S_TFVAR_MAP


@pytest.fixture
def state():
    return {}


@pytest.fixture
def client(state):
    client = Mock()

    def get_config(key):
        if key not in state:
            raise ConfigItemNotFoundException(key)
        return json.dumps(state[key])

    def update_config(key, value):
        state[key] = json.loads(value)

    client.cluster.get_config.side_effect = get_config
    client.cluster.update_config.side_effect = update_config
    client.cluster.check_sunbeam_bootstrapped.return_value = False
    client.cluster.list_terraform_plans.return_value = []
    client.cluster.get_latest_manifest.return_value = {"data": "{}"}
    return client


@pytest.fixture
def jhelper():
    helper = Mock()
    helper.get_application.return_value = Mock(
        charm_channel="1.32/stable", charm_rev=100, base=None
    )
    helper.get_charm_channels_for_revision.return_value = ["1.32/stable"]
    return helper


@pytest.fixture
def guard(client, jhelper):
    return K8STrackGuard(client, jhelper, "openstack-machines")


@pytest.fixture
def deployment(client, jhelper):
    deployment = Mock(spec=Deployment)
    deployment.juju_controller = Mock()
    deployment.openstack_machines_model = "openstack-machines"
    deployment.get_client.return_value = client
    deployment.get_juju_helper.return_value = jhelper
    deployment.get_feature_manager.return_value.get_all_feature_manifests.return_value = {}
    deployment._manifest = None
    for method in (
        "get_manifest",
        "parse_manifest",
        "resolve_k8s_channel",
        "_k8s_track_guard",
        "validate_manifest_for_infra",
        "_validate_k8s_tfvars",
        "_k8s_terraform_apply",
    ):
        getattr(deployment, method).side_effect = partial(
            getattr(Deployment, method), deployment
        )
    return deployment


def test_migrates_existing_deployment_on_a_newer_track(guard, jhelper, state, client):
    jhelper.get_application.return_value.charm_channel = "1.35/edge"
    state["TerraformVarsK8S"] = {"k8s_channel": "1.35/stable"}
    client.cluster.list_manifests.return_value = [
        {
            "manifestid": "old",
            "data": yaml.safe_dump(
                {"core": {"software": {"charms": {"k8s": {"channel": "1.32/beta"}}}}}
            ),
        }
    ]

    assert guard.resolve(None, persist=True) == "1.35/edge"
    client.cluster.list_manifests.assert_not_called()
    assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.35"}

    jhelper.get_application.return_value.charm_channel = "1.36/stable"
    with pytest.raises(CharmChannelError, match="outside Sunbeam"):
        guard.resolve(None)


def test_legacy_saved_channel_detects_out_of_band_change(guard, jhelper, state):
    state["TerraformVarsK8S"] = {"k8s_channel": "1.32/stable"}
    jhelper.get_application.return_value.charm_channel = "1.35/stable"

    with pytest.raises(CharmChannelError, match="saved Terraform"):
        guard.resolve(None, persist=True)

    assert K8S_TRACK_CONFIG_KEY not in state


def test_new_deployment_rejects_below_minimum(guard, jhelper, state):
    jhelper.get_application.side_effect = ApplicationNotFoundException()
    with pytest.raises(CharmChannelError, match="minimum"):
        guard.resolve("1.31/candidate", persist=True)
    assert K8S_TRACK_CONFIG_KEY not in state


def test_legacy_baseline_is_independent_of_new_default(guard, state, mocker):
    mocker.patch("sunbeam.core.k8s_track.K8S_CHANNEL", "1.36/stable")
    assert guard.resolve(None, persist=True) == "1.32/stable"
    assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.32"}


@pytest.mark.parametrize("track,target", [("1.32", "1.36"), ("1.36", "1.32")])
def test_rejects_both_track_change_directions(
    guard, state, jhelper, client, track, target
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": track}
    jhelper.get_application.return_value.charm_channel = f"{track}/stable"
    with pytest.raises(CharmChannelError, match="changes deployment track"):
        guard.resolve(f"{target}/edge", persist=True)
    client.cluster.update_config.assert_not_called()


@pytest.mark.parametrize("requested", [None, "1.36/stable"])
def test_recorded_track_rejects_matching_manifest_after_manual_refresh(
    guard, state, jhelper, requested
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.32"}
    jhelper.get_application.return_value.charm_channel = "1.36/stable"
    with pytest.raises(CharmChannelError, match="outside Sunbeam"):
        guard.resolve(requested)


@pytest.mark.parametrize("track", ["1.32", "1.36"])
@pytest.mark.parametrize("risk", ["stable", "candidate", "beta", "edge", "edge/fix"])
def test_allows_same_track_risks_and_branches(guard, state, jhelper, track, risk):
    state[K8S_TRACK_CONFIG_KEY] = {"track": track}
    jhelper.get_application.return_value.charm_channel = f"{track}/stable"
    assert guard.resolve(f"{track}/{risk}", persist=True) == f"{track}/{risk}"


def test_omitted_channel_retains_observed_risk(guard):
    guard.jhelper.get_application.return_value.charm_channel = "1.32/edge/fix"
    assert guard.resolve(None) == "1.32/edge/fix"


@pytest.mark.parametrize("partial_deployment", [False, True])
def test_failed_first_apply_can_change_track_only_without_deployed_state(
    deployment, client, state, jhelper, tmp_path, mocker, partial_deployment
):
    jhelper.get_application.side_effect = ApplicationNotFoundException()
    helper = TerraformHelper(
        tmp_path,
        "k8s-plan",
        DEPLOY_K8S_TFVAR_MAP,
        validate_tfvars=deployment._validate_k8s_tfvars,
        apply_context=deployment._k8s_terraform_apply,
    )

    def do_apply(channel, result):
        application_changes = Mock(charm_channel=channel)

        def apply(*args, **kwargs):
            # Terraform has received the chosen inputs but Sunbeam has yet to
            # commit the track. State may already exist after a partial apply.
            assert K8S_TRACK_CONFIG_KEY not in state
            assert state["TerraformVarsK8S"]["k8s_channel"] == channel
            if result is None or partial_deployment:
                client.cluster.list_terraform_plans.return_value = ["k8s-plan"]
            if result is not None:
                raise result
            jhelper.get_application.side_effect = None
            jhelper.get_application.return_value = application_changes

        return apply

    apply = mocker.patch.object(helper, "apply")
    apply.side_effect = do_apply("1.35/stable", TerraformException("deploy failed"))
    first = deployment.parse_manifest(
        {"core": {"software": {"charms": {"k8s": {"channel": "1.35/stable"}}}}}
    )
    with pytest.raises(TerraformException, match="deploy failed"):
        helper.update_tfvars_and_apply_tf(client, first, "TerraformVarsK8S")
    assert K8S_TRACK_CONFIG_KEY not in state

    corrected = deployment.parse_manifest(
        {"core": {"software": {"charms": {"k8s": {"channel": "1.36/candidate"}}}}}
    )
    if partial_deployment:
        with pytest.raises(TerraformException, match="changes deployment track"):
            helper.update_tfvars_and_apply_tf(client, corrected, "TerraformVarsK8S")
        apply.assert_called_once()
    else:
        apply.side_effect = do_apply("1.36/candidate", None)
        helper.update_tfvars_and_apply_tf(client, corrected, "TerraformVarsK8S")
        assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.36"}
        with pytest.raises(CharmChannelError, match="changes deployment track"):
            deployment.validate_manifest_for_infra(first)

    assert client.cluster.lock_terraform_plan.call_count == 2
    assert client.cluster.unlock_terraform_plan.call_count == 2


def test_new_deployment_without_controller_uses_default(client, state):
    assert (
        K8STrackGuard(client, None, "machines").resolve(None, persist=True)
        == "1.32/stable"
    )
    assert K8S_TRACK_CONFIG_KEY not in state


@pytest.mark.parametrize("offline", [False, True])
def test_revision_lookup_without_controller(client, mocker, offline):
    juju = mocker.patch("sunbeam.core.juju.jubilant.Juju").return_value
    juju.cli.return_value = json.dumps(
        {"channels": {"1.36": {"candidate": [{"revision": 200}]}}}
    )
    guard = K8STrackGuard(client, None, "machines")
    if offline:
        juju.cli.side_effect = jubilant.CLIError(1, ["info"], "", "offline")
        with pytest.raises(
            CharmChannelError, match="Cannot verify Kubernetes revision"
        ):
            guard.resolve("1.36/candidate", 200)
    else:
        assert guard.resolve("1.36/candidate", 200) == "1.36/candidate"
    juju.cli.assert_called_once_with(
        "info", "--format", "json", "k8s", include_model=False
    )


def test_missing_application_uses_saved_legacy_track(guard, state, jhelper, client):
    client.cluster.list_terraform_plans.return_value = ["k8s-plan"]
    state["TerraformVarsK8S"] = {"k8s_channel": "1.36/stable"}
    jhelper.get_application.side_effect = ApplicationNotFoundException()
    assert guard.resolve(None, persist=True) == "1.36/stable"
    assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.36"}


def test_missing_deployment_evidence_does_not_guess_track(guard, client, jhelper):
    client.cluster.check_sunbeam_bootstrapped.return_value = True
    jhelper.get_application.side_effect = ApplicationNotFoundException()
    with pytest.raises(CharmChannelError, match="Cannot determine the existing"):
        guard.resolve(None, persist=True)
    client.cluster.update_config.assert_not_called()


def test_initialization_rereads_record_after_lock(guard, state, client):
    # Another operation initialized the record while this request waited.
    def lock(*args):
        state[K8S_TRACK_CONFIG_KEY] = {"track": "1.36"}

    client.cluster.lock_terraform_plan.side_effect = lock
    with pytest.raises(CharmChannelError, match="outside Sunbeam"):
        guard.resolve("1.32/stable", persist=True)
    client.cluster.update_config.assert_not_called()
    assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.36"}


@pytest.mark.parametrize("channels", [[], ["1.36/stable"]])
@pytest.mark.parametrize("channel", [None, "1.32/stable"])
def test_revision_pin_cannot_bypass_track_guard(guard, jhelper, channels, channel):
    jhelper.get_charm_channels_for_revision.return_value = channels
    with pytest.raises(
        CharmChannelError, match="Cannot verify Kubernetes revision 200"
    ):
        guard.resolve(channel, 200, persist=True)


def test_revision_published_in_multiple_tracks(guard, jhelper):
    jhelper.get_charm_channels_for_revision.return_value = ["1.36/edge", "1.32/edge"]
    assert guard.resolve("1.32/edge", 200) == "1.32/edge"


def test_current_revision_needs_no_charmhub_lookup(guard, jhelper):
    assert guard.resolve(None, 100) == "1.32/stable"
    jhelper.get_charm_channels_for_revision.assert_not_called()


def test_failed_revision_lookup_is_not_permission(guard, jhelper):
    jhelper.get_charm_channels_for_revision.side_effect = JujuException("offline")
    with pytest.raises(CharmChannelError, match="Cannot verify Kubernetes revision"):
        guard.resolve(None, 200)


def test_failed_juju_lookup_is_not_fresh_deployment(guard, jhelper, client):
    jhelper.get_application.side_effect = JujuException("offline")
    with pytest.raises(CharmChannelError, match="Cannot verify the deployed"):
        guard.resolve(None, persist=True)
    client.cluster.update_config.assert_not_called()


def test_failed_database_lookup_is_not_fresh_deployment(guard, client):
    client.cluster.get_config.side_effect = ClusterServiceUnavailableException(
        "offline"
    )
    with pytest.raises(ClusterServiceUnavailableException):
        guard.resolve(None, persist=True)
    client.cluster.update_config.assert_not_called()


@pytest.mark.parametrize(
    "channel", ["stable", "latest/stable", "1.32/unknown", "", "1.32//fix"]
)
def test_requires_unambiguous_channel(guard, channel):
    with pytest.raises(CharmChannelError, match="Invalid Kubernetes channel"):
        guard.resolve(channel)


def test_track_only_channel(guard):
    assert guard.resolve("1.32") == "1.32/stable"


@pytest.mark.parametrize("record", [None, {}, {"track": ""}, {"track": "1.32/stable"}])
def test_corrupt_record_is_not_reinitialized(guard, client, state, record):
    state[K8S_TRACK_CONFIG_KEY] = record
    with pytest.raises(
        CharmChannelError, match="Invalid Kubernetes deployment track record"
    ):
        guard.resolve(None, persist=True)
    client.cluster.update_config.assert_not_called()


def test_read_only_resolution_does_not_initialize(guard, client):
    assert guard.resolve(None) == "1.32/stable"
    client.cluster.lock_terraform_plan.assert_not_called()
    client.cluster.update_config.assert_not_called()


def test_manifest_loading_does_not_query_juju(deployment, jhelper):
    jhelper.get_application.side_effect = JujuException("offline")

    manifest = deployment.get_manifest()
    assert manifest.core.software.charms["k8s"].channel is None
    assert deployment.get_manifest() is manifest
    jhelper.get_application.assert_not_called()


def test_explicit_file_is_not_ignored_after_manifest_was_cached(deployment, tmp_path):
    deployment.get_manifest()
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "core:\n  software:\n    charms:\n      k8s:\n        channel: 1.36/stable\n"
    )

    manifest = deployment.get_manifest(path)

    assert manifest.core.software.charms["k8s"].channel == "1.36/stable"
    with pytest.raises(CharmChannelError, match="changes deployment track"):
        deployment.validate_manifest_for_infra(manifest)


@pytest.mark.parametrize("deployed_channel", [None, "1.36/edge"])
def test_manifest_export_includes_effective_channel(
    deployment, state, jhelper, tmp_path, deployed_channel
):
    deployment.generate_core_config.return_value = "core:"
    deployment.get_feature_manager.return_value.features.return_value = {}
    if deployed_channel is None:
        deployment.juju_controller = None
    else:
        state[K8S_TRACK_CONFIG_KEY] = {"track": "1.36"}
        jhelper.get_application.return_value.charm_channel = deployed_channel
    path = tmp_path / "export.yaml"

    result = CliRunner().invoke(generate, ["-f", str(path)], obj=deployment)

    assert result.exit_code == 0, result.output
    assert f"channel: {deployed_channel or '1.32/stable'}" in path.read_text()
    assert deployment.get_manifest().core.software.charms["k8s"].channel is None


def test_skipped_manifest_does_not_record_track(
    deployment, client, step_context, mocker
):
    mocker.patch("sunbeam.core.manifest.infer_risk", return_value="stable")
    mocker.patch("sunbeam.core.manifest.infer_version", return_value="2026.1")
    mocker.patch(
        "sunbeam.core.manifest.embedded_manifest_path"
    ).return_value.read_bytes.return_value = b"{}"

    result = AddManifestStep(deployment).is_skip(step_context)

    assert result.result_type == ResultType.SKIPPED
    client.cluster.lock_terraform_plan.assert_not_called()
    client.cluster.update_config.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        TerraformPlanLockConflictException("busy"),
        ClusterServiceUnavailableException("offline"),
    ],
)
def test_terraform_reports_track_guard_operation_failures(tmp_path, error):
    validate = Mock(side_effect=error)
    helper = TerraformHelper(
        tmp_path, "k8s-plan", DEPLOY_K8S_TFVAR_MAP, validate_tfvars=validate
    )

    with pytest.raises(TerraformException, match=str(error)) as exc:
        helper._validate_tfvars({})

    assert exc.value.__cause__ is error


def test_fresh_manifest_can_be_corrected_before_apply(
    deployment, client, jhelper, state, step_context
):
    jhelper.get_application.side_effect = ApplicationNotFoundException()
    step = AddManifestStep(deployment)

    for channel in ("1.35/stable", "1.32/stable"):
        step.manifest_content = {
            "core": {"software": {"charms": {"k8s": {"channel": channel}}}}
        }
        assert step.run(step_context).result_type == ResultType.COMPLETED
        assert K8S_TRACK_CONFIG_KEY not in state

    assert client.cluster.add_manifest.call_count == 2
    client.cluster.lock_terraform_plan.assert_not_called()


@pytest.mark.parametrize("clear", [False, True])
def test_manifest_write_rejects_juju_drift(
    deployment, jhelper, client, state, step_context, clear
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.32"}
    jhelper.get_application.return_value.charm_channel = "1.36/stable"
    step = AddManifestStep(deployment, clear=clear)
    step.manifest_content = (
        {}
        if clear
        else {"core": {"software": {"charms": {"k8s": {"channel": "1.36/stable"}}}}}
    )
    assert step.run(step_context).result_type == ResultType.FAILED
    client.cluster.add_manifest.assert_not_called()
    client.cluster.update_config.assert_not_called()


def test_clearing_manifest_retains_record_and_invalidates_cache(
    deployment, state, step_context
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.32"}
    deployment._manifest = Mock()
    step = AddManifestStep(deployment, clear=True)
    step.manifest_content = {}
    assert step.run(step_context).result_type == ResultType.COMPLETED
    assert state[K8S_TRACK_CONFIG_KEY] == {"track": "1.32"}
    assert deployment._manifest is None


def test_refresh_resolves_omitted_channel_at_use(
    deployment, state, jhelper, client, step_context
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.36"}
    manifest = deployment.get_manifest()
    jhelper.get_application.return_value.charm_channel = "1.36/edge"
    jhelper.get_available_charm_revisions.return_value = {"amd64": 101}

    step = K8SCharmUpgradeStep(deployment, client, manifest, jhelper)
    assert step.is_skip(step_context).result_type == ResultType.COMPLETED
    assert step._decision.effective_channel == "1.36/edge"
    jhelper.get_available_charm_revisions.assert_called_once_with("k8s", "1.36/edge")
    assert manifest.core.software.charms["k8s"].channel is None


def test_legacy_patch_refresh_preflight_is_read_only(
    deployment, jhelper, client, step_context
):
    jhelper.get_application.return_value.charm_channel = "1.31/candidate"
    jhelper.get_available_charm_revisions.return_value = {"amd64": 101}

    step = K8SCharmUpgradeStep(deployment, client, deployment.get_manifest(), jhelper)

    assert step.is_skip(step_context).result_type == ResultType.COMPLETED
    assert step._decision.effective_channel == "1.31/candidate"
    client.cluster.lock_terraform_plan.assert_not_called()
    client.cluster.update_config.assert_not_called()


@pytest.mark.parametrize("phase", ["is_skip", "run"])
def test_refresh_reports_operational_errors(
    deployment, client, jhelper, step_context, phase
):
    error = TerraformPlanLockConflictException("busy")
    deployment.resolve_k8s_channel.side_effect = error
    step = K8SCharmUpgradeStep(deployment, client, deployment.get_manifest(), jhelper)

    result = getattr(step, phase)(step_context)

    assert result.result_type == ResultType.FAILED
    assert result.message == "busy"
    jhelper.run_action.assert_not_called()


def test_refresh_rechecks_track_before_running_actions(
    deployment, state, jhelper, client, step_context
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.32"}
    manifest = deployment.get_manifest()
    step = K8SCharmUpgradeStep(deployment, client, manifest, jhelper)
    jhelper.get_application.return_value.charm_channel = "1.36/stable"
    assert step.run(step_context).result_type == ResultType.FAILED
    jhelper.run_action.assert_not_called()
    jhelper.charm_refresh.assert_not_called()


@pytest.mark.parametrize("partial_update", [False, True])
def test_terraform_validation_precedes_all_writes(
    deployment, client, state, tmp_path, mocker, partial_update
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.32"}
    helper = TerraformHelper(
        tmp_path,
        "k8s-plan",
        DEPLOY_K8S_TFVAR_MAP,
        validate_tfvars=deployment._validate_k8s_tfvars,
    )
    write = mocker.patch.object(helper, "write_tfvars")
    apply = mocker.patch.object(helper, "apply")
    manifest = Manifest.model_validate(
        {"core": {"software": {"charms": {"k8s": {"channel": "1.36/stable"}}}}}
    )
    with pytest.raises(TerraformException, match="changes deployment track"):
        if partial_update:
            helper.update_partial_tfvars_and_apply_tf(
                client, manifest, ["k8s"], "TerraformVarsK8S"
            )
        else:
            helper.update_tfvars_and_apply_tf(client, manifest, "TerraformVarsK8S")
    write.assert_not_called()
    apply.assert_not_called()
    client.cluster.update_config.assert_not_called()


def test_terraform_read_only_validation_does_not_record_track(
    deployment, client, state, tmp_path, mocker
):
    helper = TerraformHelper(
        tmp_path,
        "k8s-plan",
        DEPLOY_K8S_TFVAR_MAP,
        validate_tfvars=deployment._validate_k8s_tfvars,
    )
    write = mocker.patch.object(helper, "write_tfvars")

    tfvars = helper.update_tfvars(client, Manifest(), "TerraformVarsK8S", persist=False)

    assert tfvars["k8s_channel"] == "1.32/stable"
    write.assert_called_once_with(tfvars)
    assert K8S_TRACK_CONFIG_KEY not in state
    client.cluster.lock_terraform_plan.assert_not_called()
    client.cluster.update_config.assert_not_called()


def test_terraform_omitted_channel_preserves_deployed_track_and_risk(
    deployment, client, state, jhelper, tmp_path, mocker
):
    state[K8S_TRACK_CONFIG_KEY] = {"track": "1.36"}
    state["TerraformVarsK8S"] = {
        "_computed_keys": ["k8s_channel"],
        "k8s_channel": "1.36/stable",
    }
    jhelper.get_application.return_value.charm_channel = "1.36/edge"
    helper = TerraformHelper(
        tmp_path,
        "k8s-plan",
        DEPLOY_K8S_TFVAR_MAP,
        validate_tfvars=deployment._validate_k8s_tfvars,
    )
    write = mocker.patch.object(helper, "write_tfvars")
    manifest = deployment.get_manifest()

    helper.update_tfvars(client, manifest, "TerraformVarsK8S")

    assert write.call_args.args[0]["k8s_channel"] == "1.36/edge"
    assert state["TerraformVarsK8S"]["k8s_channel"] == "1.36/edge"
    assert manifest.core.software.charms["k8s"].channel is None


def test_terraform_computed_channel_is_also_checked(
    deployment, client, tmp_path, mocker
):
    helper = TerraformHelper(
        tmp_path,
        "k8s-plan",
        DEPLOY_K8S_TFVAR_MAP,
        validate_tfvars=deployment._validate_k8s_tfvars,
    )
    apply = mocker.patch.object(helper, "apply")
    with pytest.raises(TerraformException, match="changes deployment track"):
        helper.update_tfvars_and_apply_tf(
            client, Manifest(), "TerraformVarsK8S", {"k8s_channel": "1.36/stable"}
        )
    apply.assert_not_called()
