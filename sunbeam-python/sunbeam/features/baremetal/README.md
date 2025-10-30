# Baremetal service

This feature provides Baremetal service for Sunbeam. It's based on [Ironic](https://docs.openstack.org/ironic/latest/), the bare metal provisioning service for OpenStack.

## Installation

To enable the Baremetal service, you need an already bootstraped Sunbeam instance, and the storage role enabled. Then, you can install the feature with:

```bash
sunbeam enable baremetal
```

The feature will be configured based on the cluster's manifest file. Alternatively, a different manifest file can be specified during the feature enablement:

```bash
sunbeam enable --manifest baremetal-manifest.yaml baremetal
```

Sample `baremetal-manifest.yaml` file:

```yaml
features:
  baremetal:
    software:
      charms:
        ironic-conductor-k8s:
          channel: 2025.1/edge
        ironic-k8s:
          channel: 2025.1/edge
        nova-ironic-k8s:
          channel: 2025.1/edge
    config:
      shards: ["foo", "lish"]
```

**Note**: Rerunning the `sunbeam enable baremetal` command with a different manifest file will replace the previously deployed feature configuration (e.g.: deployed `nova-ironic` shards).

## Additional commands

Additional commands will be available after the feature has been enabled:

- `sunbeam baremetal shard add SHARD`: Add a new Ironic nova-compute shard.
- `sunbeam baremetal shard list`: List Ironic nova-compute shards.
- `sunbeam baremetal shard delete SHARD`: Delete Ironic nova-compute shard.
- `sunbeam baremetal conductor-groups add GROUP-NAME`: Add ironic-conductor group.
- `sunbeam baremetal conductor-groups list`: List ironic-conductor groups.
- `sunbeam baremetal conductor-groups delete GROUP-NAME`: Delete ironic-conductor group.

## Contents

This feature will install the following services:
- Ironic: Bare metal provisioning service API for OpenStack [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/ironic-consolidated)
- Nova Ironic: A nova-compute service configured with the Ironic driver [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/nova-ironic-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/nova-ironic)
- Ironic Conductor: Does the bulk of the bare metal deployment work [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-conductor-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/ironic-conductor)
- MySQL Routers for Ironic [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the feature, run:

```bash
sunbeam disable baremetal
```
