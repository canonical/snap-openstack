# MicroStack

## Small footprint, K8S native OpenStack

MicroStack is a modern cloud solution that uses snaps, Juju,
and Kubernetes to deploy and manage OpenStack.

Snaps are used to deploy and perform major cluster operations where
Juju charmed operators are leveraged internally to manage individual
cloud services. Traditional charms oversee the cloud data plane and
Kubernetes charms govern the cloud control plane.

Deploying and managing an OpenStack cloud is generally considered to
be a challenging endeavour. MicroStack reduces the complexity traditionally
imposed upon cloud administrators by automating cloud operations where
possible and reaping the benefits of Kubernetes-based API services.

MicroStack is designed from the ground up to accommodate users of
varying skill levels. It is appropriate for public, regional, and
private clouds, and can satisfy a wide range of use cases: from small
single-node development environments through to large multi-node,
MAAS-based, enterprise-grade solutions.

See the full [MicroStack documentation][microstack-docs].

## Contributing / developer documentation

Please see the [docs](./docs) directory in this repository.

## Reporting a bug

Please report bugs to the [OpenStack Snap][microstack] project on Launchpad.

[microstack-docs]: https://canonical-openstack.readthedocs-hosted.com/en/latest/
[microstack]: https://bugs.launchpad.net/snap-openstack
