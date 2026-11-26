# sunbeam-microcluster

The cluster daemon for Sunbeam based on MicroCluster library

# Building sunbeam-microcluster

The MicroCluster library makes use of dqlite which provides a raft based
sqlite compatible database for shared state across the Sunbeam cluster.

Build with Go 1.27 and dqlite 1.17. On Ubuntu 24.04 or 26.04, run
from the repository root:

    bash .github/scripts/install-dqlite.sh
    make -C sunbeam-microcluster build

The script builds dqlite from the `lts-1.17.x` branch used by the snap.

## Upgrading clusterd from MicroCluster v2.2.5 to v3.1.3

Fresh deployments initialize the current schema during bootstrap. For
existing deployments, refresh the OpenStack snap in place. Snapd
restarts `openstack.clusterd`, and `sunbeamd` opens the existing database,
certificates, and truststore under `$SNAP_COMMON/state`. Keep that state
directory and the existing cluster membership.

MicroCluster v2.2.5 already uses `core_token_records`. Both versions have
the same internal schema version, so the library upgrade requires no
additional schema migration. OpenStack release upgrades can include
separate Sunbeam schema updates.
[MicroCluster v2.2.5 schema](https://github.com/canonical/microcluster/blob/v2.2.5/internal/db/update/update.go) ·
[MicroCluster v3.1.3 schema](https://github.com/canonical/microcluster/blob/v3.1.3/internal/db/update/update.go)

### Readiness and recovery

Start with healthy membership and quorum. When the schema version
stays the same, refresh one clusterd member at a time and wait for it to
come back online before refreshing the next member.

Complete schema-changing upgrades in one maintenance window. Refreshed
members return HTTP 503 for database-backed requests until all members
start with the new schema version. Older members that are still running
can serve requests while dqlite has quorum. Manual clients on refreshed
nodes use the local daemon, and MAAS clients can also see 503 if their
configured endpoint is a refreshed member.

Continue to the next member once the current refresh completes and the
daemon reports that it is waiting for the schema update. Check that all
members are online and cluster state is readable after the final member
starts. Once the new schema commits, a daemon that supports only the
earlier schema cannot reopen the database. Plan recovery at the cluster
level.
[MicroCluster schema upgrade behavior](https://github.com/canonical/microcluster/blob/v3.1.3/doc/upgrades.md)

### Verifying a three-member MAAS refresh

Before refreshing, record the cluster membership and an existing Sunbeam
configuration value. Use each unit's local control socket for the checks
so that traffic cannot be routed to a different member. For example, read
an existing key with `GET /1.0/config/<key>` over
`/var/snap/openstack/common/state/control.socket`.

1. For a same-schema upgrade, run the `sunbeam-clusterd` charm's
   `refresh-snap` action on one unit. Check that the refreshed unit can
   read the recorded value and has rejoined the cluster before running
   the action on the next unit. Run the same checks during a rolling
   `snap revert openstack` on the same schema.
2. For a schema-changing upgrade, check that the refreshed unit either
   reads the recorded value or explicitly reports `Database is waiting
   for an upgrade` with HTTP 503 before advancing. A stopped daemon,
   generic 503, or failed connection needs investigation. Check that the
   remaining old units can still read while the cluster has quorum.
   Once all units are refreshed, check the recorded value and membership
   from each unit.
3. Create an unused join token before refresh and revoke it after
   refresh. Create a second token after refresh and revoke it after a
   same-schema revert. Check that each revoked token disappears from
   the token list.

### Join-token revocation API

Use the route for the version running on the node you contact. Sunbeam's
CLI ships with the matching client for its local daemon.

| MicroCluster version | Request over the local control socket |
| --- | --- |
| v2.2.5 | `DELETE /core/1.0/tokens/<name>` |
| v3.1.3 | `DELETE /core/control/tokens/<name>` |

Token creation and listing still use `/core/control/tokens` in both
versions. A mismatched DELETE route returns HTTP 404, including when the
token still exists. Check the daemon version before interpreting a 404
from a direct API call.
