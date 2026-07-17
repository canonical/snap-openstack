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
from sunbeam.steps.backup_restore import (
    BACKUP_COMPONENTS,
    MYSQL_CHARM,
    S3_ENDPOINT,
    S3_INTERFACE,
    VAULT_CHARM,
    ActionTarget,
    BackupComponent,
    BackupInventory,
    BackupOutcome,
    BackupResult,
    DiscoverBackupApplicationsStep,
    ListBackupsStep,
    ResolveActionTargetsStep,
    RunBackupsStep,
    ValidateStep,
    WriteBackupInventoryManifestStep,
    WriteBackupManifestStep,
    _BackupAppStep,
    _build_vault_backup_plan,
    _build_vault_restore_plan,
    _component_for,
    _parse_backup,
    _parse_mysql_backups,
    _parse_vault_backups,
    _resolve_mysql_backup_target,
    _resolve_vault_backup_target,
)


def _app_status(charm_name, units=None, relations=None):
    app = Mock()
    app.charm_name = charm_name
    app.units = units or {}
    app.relations = relations or {}
    app.app_status.current = "active"
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
    return {"status": {"defaultreplicaset": {"topology": topology}}}


class TestBackupResult:
    def test_construction_with_error_does_not_raise(self):
        result = BackupResult(
            app="keystone-mysql",
            unit="keystone-mysql/0",
            component=MYSQL_CHARM,
            error="boom",
        )
        assert result.error == "boom"
        assert result.backup is None


class TestRegistry:
    def test_registry_contains_mysql_and_vault(self):
        names = {c.name for c in BACKUP_COMPONENTS}
        assert names == {MYSQL_CHARM, VAULT_CHARM}

    def test_components_have_restore_plans(self):
        for component in BACKUP_COMPONENTS:
            assert component.build_restore_plan is not None


class TestResolveMySQLTarget:
    def test_picks_secondary_unit(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.run_action.return_value = _cluster_status(1)
        app = _app_status(
            "mysql-k8s", units={"keystone-mysql/0": Mock(), "keystone-mysql/1": Mock()}
        )
        jhelper.get_application.return_value = app

        target = _resolve_mysql_backup_target(
            jhelper, "keystone-mysql", "openstack", force=False
        )

        assert target is not None
        assert target.unit == "keystone-mysql/1"
        assert target.component == MYSQL_CHARM

    def test_falls_back_to_leader_when_no_secondary(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "cinder-mysql/0"
        jhelper.run_action.return_value = {
            "status": {
                "defaultreplicaset": {
                    "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                }
            }
        }
        jhelper.get_application.return_value = _app_status(
            "mysql-k8s", units={"cinder-mysql/0": Mock()}
        )

        target = _resolve_mysql_backup_target(
            jhelper, "cinder-mysql", "openstack", force=False
        )

        assert target is not None
        assert target.unit == "cinder-mysql/0"

    def test_skips_on_action_failure_without_force(self):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.get_application.return_value = _app_status(
            "mysql-k8s", units={"keystone-mysql/0": Mock()}
        )
        jhelper.run_action.side_effect = ActionFailedException("failed")

        target = _resolve_mysql_backup_target(
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

        target = _resolve_mysql_backup_target(
            jhelper, "keystone-mysql", "openstack", force=True
        )

        assert target is not None
        assert target.unit == "keystone-mysql/0"

    def test_skips_when_no_leader(self):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")

        target = _resolve_mysql_backup_target(
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

        target = _resolve_vault_backup_target(
            jhelper, "vault", "openstack", force=False
        )

        assert target is not None
        assert target.unit == "vault/0"
        assert target.component == VAULT_CHARM

    def test_skips_when_no_leader(self):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException("no leader")

        target = _resolve_vault_backup_target(
            jhelper, "vault", "openstack", force=False
        )

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
        assert sorted(result.message[MYSQL_CHARM]) == ["keystone-mysql", "nova-mysql"]
        assert result.message[VAULT_CHARM] == ["vault"]

    def test_fails_on_model_error(self, step_context):
        jhelper = Mock()
        jhelper.get_model_status.side_effect = ModelNotFoundException("missing")

        result = DiscoverBackupApplicationsStep(jhelper).run(step_context)

        assert result.result_type == ResultType.FAILED

    def test_non_active_app_is_still_discovered(self, step_context):
        """Discovery is state-agnostic; validation filters non-active apps."""
        jhelper = Mock()
        mysql = _app_status("mysql-k8s")
        mysql.app_status.current = "blocked"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = DiscoverBackupApplicationsStep(jhelper).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message[MYSQL_CHARM] == ["keystone-mysql"]


class TestResolveActionTargetsStep:
    def test_resolves_leaders_and_skips_unresolvable(self, step_context):
        jhelper = Mock()

        def _leader(app, model):
            if app == "broken-mysql":
                raise LeaderNotFoundException("no leader")
            return f"{app}/0"

        jhelper.get_leader_unit.side_effect = _leader

        discovered = {
            MYSQL_CHARM: ["keystone-mysql", "broken-mysql"],
            VAULT_CHARM: ["vault"],
        }
        result = ResolveActionTargetsStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        apps = {t.app for t in result.message["targets"]}
        assert apps == {"keystone-mysql", "vault"}
        assert all(t.unit.endswith("/0") for t in result.message["targets"])
        assert result.message["unresolved"] == [
            {"app": "broken-mysql", "component": MYSQL_CHARM}
        ]


class TestValidateStep:
    def test_partitions_by_active_and_s3(self, step_context):
        jhelper = Mock()
        s3 = Mock()
        s3.interface = S3_INTERFACE
        ready = _app_status("mysql-k8s", relations={S3_ENDPOINT: [s3]})
        no_s3 = _app_status("mysql-k8s")
        inactive = _app_status("mysql-k8s", relations={S3_ENDPOINT: [s3]})
        inactive.app_status.current = "blocked"
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": ready,
                "nova-mysql": no_s3,
                "glance-mysql": inactive,
            }
        )

        discovered = {MYSQL_CHARM: ["keystone-mysql", "nova-mysql", "glance-mysql"]}
        result = ValidateStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message["valid"][MYSQL_CHARM] == ["keystone-mysql"]
        assert result.message["failures"]["nova-mysql"] == ["s3-relation"]
        assert result.message["failures"]["glance-mysql"] == ["active"]

    def test_missing_app_fails_all_checks(self, step_context):
        jhelper = Mock()
        jhelper.get_model_status.return_value = _model_status({})

        discovered = {MYSQL_CHARM: ["keystone-mysql"]}
        result = ValidateStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message["valid"][MYSQL_CHARM] == []
        assert "keystone-mysql" in result.message["failures"]


class TestBackupAppStep:
    def test_success_records_backup_result(self, step_context):
        jhelper = Mock()
        jhelper.run_action.return_value = {"backup-id": "id-1"}
        component = _component_for(MYSQL_CHARM)
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/1", MYSQL_CHARM, "create-backup"
        )

        step = _BackupAppStep(jhelper, component, target)
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.result is not None
        assert step.result.backup is not None
        assert step.result.backup.success is True
        assert step.result.backup.backup_id == "id-1"

    def test_timeout_is_marked_in_progress(self, step_context):
        jhelper = Mock()
        jhelper.run_action.side_effect = Exception(
            "timed out waiting for results from: unit nova-mysql/0"
        )
        component = _component_for(MYSQL_CHARM)
        target = ActionTarget(
            "nova-mysql", "nova-mysql/0", MYSQL_CHARM, "create-backup"
        )

        step = _BackupAppStep(jhelper, component, target)
        step.run(step_context)

        assert step.result is not None
        assert step.result.error is not None
        assert step.result.backup is not None
        assert step.result.backup.success is None


class TestRunBackupsStep:
    def test_resolves_and_aggregates_mixed_results(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.side_effect = lambda app, model: _app_status(
            "mysql-k8s", units={f"{app}/0": Mock(), f"{app}/1": Mock()}
        )

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return _cluster_status(1)
            if unit == "glance-mysql/1":
                raise ActionFailedException("backup failed")
            return {"backup-id": f"backup-{unit.replace('/', '-')}"}

        jhelper.run_action.side_effect = _run_action
        discovered = {
            MYSQL_CHARM: ["keystone-mysql", "glance-mysql"],
            VAULT_CHARM: ["vault"],
        }

        result = RunBackupsStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        by_app = {r.app: r for r in result.message}
        assert by_app["keystone-mysql"].backup is not None
        assert by_app["keystone-mysql"].backup.success is True
        assert by_app["glance-mysql"].backup is None
        assert by_app["glance-mysql"].error is not None
        assert by_app["vault"].backup is not None
        assert by_app["vault"].backup.success is True

    def test_force_passes_force_param_only_to_mysql(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.side_effect = lambda app, model: _app_status(
            "mysql-k8s", units={f"{app}/0": Mock()}
        )

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            return {"backup-id": "id"}

        jhelper.run_action.side_effect = _run_action
        discovered = {MYSQL_CHARM: ["keystone-mysql"], VAULT_CHARM: ["vault"]}

        RunBackupsStep(jhelper, discovered, force=True).run(step_context)

        params_by_unit = {
            call.args[0]: call.args[3]
            for call in jhelper.run_action.call_args_list
            if call.args[2] == "create-backup"
        }
        assert params_by_unit["keystone-mysql/0"] == {"force": True}
        # vault does not support force, but we still pass it as a param ignored by Juju
        assert params_by_unit["vault/0"] == {"force": True}

    def test_timeout_is_marked_as_in_progress(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.side_effect = lambda app, model: _app_status(
            "mysql-k8s", units={f"{app}/0": Mock()}
        )

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            raise Exception("timed out waiting for results from: unit nova-mysql/0")

        jhelper.run_action.side_effect = _run_action
        discovered = {MYSQL_CHARM: ["nova-mysql"]}

        result = RunBackupsStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        backup_result = result.message[0]
        assert backup_result.error is not None
        assert backup_result.backup is not None
        assert backup_result.backup.success is None


class TestListBackupsParsing:
    def test_parse_mysql_backup_ids_filters_finished_entries(self):
        action_result = {
            "backups": (
                "backup-id             | backup-type | backup-status\n"
                "--------------------------------------------------\n"
                "2026-07-15T00:00:00Z  | physical    | finished\n"
                "2026-07-14T00:00:00Z  | physical    | failed"
            )
        }

        backups = _parse_mysql_backups(action_result)

        assert [b.backup_id for b in backups] == [
            "2026-07-15T00:00:00Z",
            "2026-07-14T00:00:00Z",
        ]
        assert [b.success for b in backups] == [True, False]

    def test_parse_vault_backup_ids_json_array(self):
        action_result = {
            "backup-ids": json.dumps(
                [
                    "vault-backup-openstack-2026-07-15-00-03-28",
                    "vault-backup-openstack-2026-07-14-00-03-28",
                ]
            )
        }

        backups = _parse_vault_backups(action_result)

        assert [b.backup_id for b in backups] == [
            "vault-backup-openstack-2026-07-15-00-03-28",
            "vault-backup-openstack-2026-07-14-00-03-28",
        ]
        assert all(b.success for b in backups)


class TestListBackupsStep:
    def test_collects_backup_ids_by_target(self, step_context):
        jhelper = Mock()

        def _run_action(unit, model, action, params=None, timeout=None):
            assert action == "list-backups"
            if unit == "keystone-mysql/0":
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | finished"
                    )
                }
            return {
                "backup-ids": json.dumps(["vault-backup-openstack-2026-07-15-00-03-28"])
            }

        jhelper.run_action.side_effect = _run_action
        targets = [
            ActionTarget(
                "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "list-backups"
            ),
            ActionTarget("vault", "vault/0", VAULT_CHARM, "list-backups"),
        ]

        result = ListBackupsStep(jhelper, targets).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        by_app = {r.app: r for r in result.message}
        assert by_app["keystone-mysql"].error is None
        assert [b.backup_id for b in by_app["keystone-mysql"].backups] == [
            "2026-07-15T00:00:00Z"
        ]
        assert by_app["vault"].error is None
        assert [b.backup_id for b in by_app["vault"].backups] == [
            "vault-backup-openstack-2026-07-15-00-03-28"
        ]

    def test_collects_errors_without_raising(self, step_context):
        jhelper = Mock()
        jhelper.run_action.side_effect = Exception("boom")
        targets = [
            ActionTarget(
                "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "list-backups"
            )
        ]

        result = ListBackupsStep(jhelper, targets).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message[0].backups is None
        assert result.message[0].error == "boom"


class TestWriteBackupManifestStep:
    def test_writes_manifest(self, step_context, tmp_path):
        results = [
            BackupResult(
                "keystone-mysql",
                "keystone-mysql/1",
                MYSQL_CHARM,
                BackupOutcome("id-1", success=True),
            ),
            BackupResult("glance-mysql", "glance-mysql/1", MYSQL_CHARM, None, "err"),
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


class TestWriteBackupInventoryManifestStep:
    def test_writes_inventory_manifest(self, step_context, tmp_path):
        results = [
            BackupInventory(
                app="keystone-mysql",
                unit="keystone-mysql/1",
                component=MYSQL_CHARM,
                backups=[BackupOutcome("2026-07-15T00:00:00Z", success=True)],
            ),
            BackupInventory(
                app="vault",
                unit="vault/0",
                component=VAULT_CHARM,
                error="failed",
            ),
        ]
        step = WriteBackupInventoryManifestStep(
            results, "2026-07-15T00:04:28+00:00", manifest_dir=tmp_path
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        written = list(tmp_path.glob("backup-inventory-*.yaml"))
        assert len(written) == 1


class TestExtensibility:
    """Adding a component is a registration change, not a code change (FR-021)."""

    def test_new_component_flows_through_generic_pipeline(
        self, step_context, monkeypatch
    ):
        def _resolve_fake(jhelper, app, model, force):
            return ActionTarget(app, f"{app}/0", "fake-charm", "create-backup")

        fake = BackupComponent(
            name="fake-charm",
            resolve_backup_target=_resolve_fake,
            parse_backup_list=_parse_vault_backups,
            parse_backup=_parse_backup,
            build_backup_plan=_build_vault_backup_plan,
            build_restore_plan=_build_vault_restore_plan,
        )
        components = BACKUP_COMPONENTS + [fake]
        monkeypatch.setattr(
            "sunbeam.steps.backup_restore.BACKUP_COMPONENTS", components
        )

        jhelper = Mock()
        jhelper.get_model_status.return_value = _model_status(
            {"my-fake": _app_status("fake-charm")}
        )
        jhelper.run_action.return_value = {"backup-id": "fake-backup"}

        discover = DiscoverBackupApplicationsStep(jhelper, components=components).run(
            step_context
        )
        assert discover.message["fake-charm"] == ["my-fake"]

        run = RunBackupsStep(jhelper, {"fake-charm": ["my-fake"]}).run(step_context)
        assert run.message[0].component == "fake-charm"
        assert run.message[0].backup is not None
        assert run.message[0].backup.success is True
        assert run.message[0].backup.backup_id == "fake-backup"
