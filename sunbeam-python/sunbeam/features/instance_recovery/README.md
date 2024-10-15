# Instance Recovery service

This feature provides Instance Recovery service for Sunbeam. It's based on [Masakari](https://docs.openstack.org/masakari/latest/), an Instance High Availability service for OpenStack.

## Installation

To enable the Instance Recovery service, you need an already bootstraped Sunbeam instance and the Consul feature. Then, you can install the feature with:

```bash
sunbeam enable instance-recovery
```

## Contents

This feature will install the following services:
- Maskari: Instance HA service for OpenStack [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/masakari-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/masakari-consolidated)
- MySQL Router for Barbican [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the feature, run:

```bash
sunbeam disable instance-recovery
```
