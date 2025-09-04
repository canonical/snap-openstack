# Shared Filesystems service

This feature provides Shared Filesystems service for Sunbeam. It's based on [Manila](https://docs.openstack.org/manila/latest/), the Shared Filesystems as a Service for OpenStack.

## Installation

To enable the Shared Filesystems service, you need an already bootstraped Sunbeam instance, and the storage role enabled. Then, you can install the feature with:

```bash
sunbeam enable shared-filesystem
```

## Contents

This feature will install the following services:
- Manila: Shared Filesystems as a Service for OpenStack [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/manila-consolidated)
- Manila CEPHFS: CEPH NFS storage backend provider for Manila [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-cephfs-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/manila-share)
- MySQL Router for Manila [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the feature, run:

```bash
sunbeam disable shared-filesystem
```
