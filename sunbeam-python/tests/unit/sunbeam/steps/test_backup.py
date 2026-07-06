# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    LeaderNotFoundException,
    ModelNotFoundException,
)
from sunbeam.steps.backup import (
    BACKUP_COMPONENTS,
    BackupComponent,
    BackupResult,
    BackupTarget,
    DiscoverBackupApplicationsStep,
    ResolveBackupTargetsStep,
    RunBackupsStep,
    WriteBackupManifestStep,
    _resolve_mysql_target,
    _resolve_vault_target,
)


def _app_status(charm_name, units=None):
    app = Mock()
    app.charm_name = charm_name
    app.units = units or {}
    return app


def _model_status(apps):
    status = Mock()
    status.apps = apps
    return status


def _cluster_status(secondary_ordinal):
    topology = {
        "mysql-0.mysql-endpoints": {"memberrole": "PRIMARY"},
        f"mysql-{secondary_ordinal}.mysql-endpoints": {"memberrole": "SECONDARY"},
    }
    return {"status": json.dumps({"defaultreplicaset": {"topology": topology}})}


class TestBackupResult:
    def test_construction_with_error_does_not_raise(self):
        result = BackupResult(
            app="keystone-mysql",
            unit="keystone-mysql/0",
            component="mysql",
            success=False,
            error="boom",
        )
        assert result.success is False
        assert result.backup_id is None
        assert result.error == "boom"


class TestRegistry:
    def test_registry_contains_mysql_and_vault(self):
        names = {c.name for c in BACKUP_COMPONENTS}
        assert names == {"mysql", "vault"}


class TestResolveMySQLTarget:
    def test_picks_secondary_unit(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.run_action.return_value = _cluster_status(1)
        app = _app_status(
            "mysql-k8s", units={"keystone-mysql/0": Mock(), "keystone-mysql/1": Mock()}
        )
        jhelper.get_application.return_value = app

        target = _resolve_mysql_target(
            jhelper, "keystone-mysql", "openstack", force=False
        )

        assert target is not None
        assert target.unit == "keystone-mysql/1"
        assert target.is_replica is True
        assert target.scale == 2

    def test_falls_back_to_leader_when_no_secondary(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "cinder-mysql/0"
        jhelper.run_action.return_value = {
            "status": json.dumps(
                {
                    "defaultreplicaset": {
                        "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                    }
                }
            )
        }
        jhelper.get_application.return_value = _app_status(
            "mysql-k8s", units={"cinder-mysql/0": Mock()}
        )

        target = _resolve_mysql_target(
            jhelper, "cinder-mysql", "openstack", force=False
        )

        assert target is not None
        assert target.unit == "cinder-mysql/0"
        assert target.is_replica is False
        assert target.scale == 1

    def test_skips_on_action_failure_without_force(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.get_application.return_value = _app_status(
            "mysql-k8s", units={"keystone-mysql/0": Mock()}
        )
        jhelper.run_action.side_effect = ActionFailedException("failed")

        target = _resolve_mysql_target(
            jhelper, "keystone-mysql", "openstack", force=False
        )

        assert target is None

    def test_uses_leader_on_action_failure_with_force(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.get_application.return_value = _app_status(
            "mysql-k8s", units={"keystone-mysql/0": Mock()}
        )
        jhelper.run_action.side_effect = ActionFailedException("failed")

        target = _resolve_mysql_target(
            jhelper, "keystone-mysql", "openstack", force=True
        )

        assert target is not None
        assert target.unit == "keystone-mysql/0"
        assert target.is_replica is False
        assert target.scale == 1

    def test_skips_when_no_leader(self):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")

        target = _resolve_mysql_target(
            jhelper, "keystone-mysql", "openstack", force=True
        )

        assert target is None


class TestResolveVaultTarget:
    def test_resolves_leader(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.get_application.return_value = _app_status(
            "vault-k8s", units={"vault/0": Mock()}
        )

        target = _resolve_vault_target(jhelper, "vault", "openstack", force=False)

        assert target is not None
        assert target.unit == "vault/0"
        assert target.component == "vault"
        assert target.is_replica is False
        assert target.scale == 1

    def test_skips_when_no_leader(self):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")

        target = _resolve_vault_target(jhelper, "vault", "openstack", force=False)

        assert target is None


class TestDiscoverBackupApplicationsStep:
    def test_discovers_by_charm_name(self, step_context):
        jhelper = Mock()
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": _app_status("mysql-k8s"),
                "nova-mysql": _app_status("mysql-k8s"),
                "vault": _app_status("vault-k8s"),
                "keystone": _app_status("keystone-k8s"),
            }
        )

        result = DiscoverBackupApplicationsStep(jhelper).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert sorted(result.message["mysql"]) == ["keystone-mysql", "nova-mysql"]
        assert result.message["vault"] == ["vault"]

    def test_fails_on_model_error(self, step_context):
        jhelper = Mock()
        jhelper.get_model_status.side_effect = ModelNotFoundException("missing")

        result = DiscoverBackupApplicationsStep(jhelper).run(step_context)

        assert result.result_type == ResultType.FAILED


class TestResolveBackupTargetsStep:
    def test_flattens_targets_and_skips_unresolvable(self, step_context):
        jhelper = Mock()

        def _leader(app, model):
            if app == "broken-mysql":
                raise LeaderNotFoundException("no leader")
            return f"{app}/0"

        jhelper.get_leader_unit.side_effect = _leader
        jhelper.run_action.return_value = _cluster_status(1)
        jhelper.get_application.side_effect = lambda app, model: _app_status(
            "mysql-k8s", units={f"{app}/0": Mock(), f"{app}/1": Mock()}
        )

        discovered = {"mysql": ["keystone-mysql", "broken-mysql"], "vault": ["vault"]}
        result = ResolveBackupTargetsStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        apps = {t.app for t in result.message}
        assert apps == {"keystone-mysql", "vault"}


class TestRunBackupsStep:
    def test_skips_when_no_targets(self, step_context):
        step = RunBackupsStep(Mock(), [])
        assert step.is_skip(step_context).result_type == ResultType.SKIPPED

    def test_aggregates_mixed_results(self, step_context):
        jhelper = Mock()

        def _run_action(unit, model, action, params=None, timeout=None):
            if unit == "glance-mysql/1":
                raise ActionFailedException("backup failed")
            return {"backup-id": f"backup-{unit.replace('/', '-')}"}

        jhelper.run_action.side_effect = _run_action
        targets = [
            BackupTarget(
                "keystone-mysql", "keystone-mysql/1", "mysql", "create-backup", 2
            ),
            BackupTarget("glance-mysql", "glance-mysql/1", "mysql", "create-backup", 2),
            BackupTarget("vault", "vault/0", "vault", "create-backup", 1),
        ]

        result = RunBackupsStep(jhelper, targets).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        by_app = {r.app: r for r in result.message}
        assert by_app["keystone-mysql"].success is True
        assert by_app["keystone-mysql"].backup_id == "backup-keystone-mysql-1"
        assert by_app["glance-mysql"].success is False
        assert by_app["glance-mysql"].error is not None
        assert by_app["vault"].success is True

    def test_force_passes_force_param_only_to_mysql(self, step_context):
        jhelper = Mock()
        jhelper.run_action.return_value = {"backup-id": "id"}
        targets = [
            BackupTarget(
                "keystone-mysql", "keystone-mysql/1", "mysql", "create-backup", 2
            ),
            BackupTarget("vault", "vault/0", "vault", "create-backup", 1),
        ]

        RunBackupsStep(jhelper, targets, force=True).run(step_context)

        params_by_unit = {
            call.args[0]: call.args[3] for call in jhelper.run_action.call_args_list
        }
        assert params_by_unit["keystone-mysql/1"] == {"force": True}
        assert params_by_unit["vault/0"] is None


class TestWriteBackupManifestStep:
    def test_writes_manifest(self, step_context, tmp_path):
        results = [
            BackupResult("keystone-mysql", "keystone-mysql/1", "mysql", True, "id-1"),
            BackupResult("glance-mysql", "glance-mysql/1", "mysql", False, None, "err"),
        ]
        step = WriteBackupManifestStep(
            results, "2026-04-09T14:22:01+00:00", manifest_dir=tmp_path
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        written = list(tmp_path.glob("backup-manifest-*.yaml"))
        assert len(written) == 1
        import yaml

        data = yaml.safe_load(written[0].read_text())
        assert data["summary"] == {"succeeded": 1, "failed": 1}
        assert data["dispatched_at"] == "2026-04-09T14:22:01+00:00"
        assert {r["app"] for r in data["results"]} == {
            "keystone-mysql",
            "glance-mysql",
        }


class TestExtensibility:
    """Adding a component is a registration change, not a code change (FR-021)."""

    def test_new_component_flows_through_generic_pipeline(
        self, step_context, monkeypatch
    ):
        def _resolve_fake(jhelper, app, model, force):
            return BackupTarget(app, f"{app}/0", "fake", "create-backup", 1)

        fake = BackupComponent(
            name="fake",
            charm_names=["fake-charm"],
            action="create-backup",
            resolve_target=_resolve_fake,
        )
        components = BACKUP_COMPONENTS + [fake]
        monkeypatch.setattr("sunbeam.steps.backup.BACKUP_COMPONENTS", components)

        jhelper = Mock()
        jhelper.get_model_status.return_value = _model_status(
            {"my-fake": _app_status("fake-charm")}
        )
        jhelper.run_action.return_value = {"backup-id": "fake-backup"}

        discover = DiscoverBackupApplicationsStep(jhelper, components=components).run(
            step_context
        )
        assert discover.message["fake"] == ["my-fake"]

        resolve = ResolveBackupTargetsStep(jhelper, discover.message).run(step_context)
        fake_targets = [t for t in resolve.message if t.component == "fake"]
        assert len(fake_targets) == 1

        run = RunBackupsStep(jhelper, fake_targets).run(step_context)
        assert run.message[0].component == "fake"
        assert run.message[0].success is True
        assert run.message[0].backup_id == "fake-backup"
