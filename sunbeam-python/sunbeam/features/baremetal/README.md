# Baremetal service

This feature provides Baremetal service for Sunbeam. It's based on [Ironic](https://docs.openstack.org/ironic/latest/), the bare metal provisioning service for OpenStack.

## Installation

To enable the Baremetal service, you need an already bootstrapped Sunbeam instance, and the storage role enabled. Then, you can install the feature with:

```bash
sunbeam enable baremetal
```

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
