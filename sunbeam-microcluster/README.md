# sunbeam-microcluster

The cluster daemon for Sunbeam based on MicroCluster library

# Building sunbeam-microcluster

The MicroCluster library makes use of dqlite which provides a raft based
sqlite compatible database for shared state across the Sunbeam cluster.

This requires a few dependencies to be installed:

    sudo add-apt-repository -y ppa:dqlite/dev
    sudo apt install gcc make libdqlite1.17-dev -y
    sudo snap install --channel 1.21 --classic go

after which is possible to build sunbeam-microcluster:

    make build

Have fun!

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
