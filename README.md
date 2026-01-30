# Canonical OpenStack

## Your own enterprise-grade cloud platform

This snap installs Sunbeam – a modern framework for automating OpenStack
deployment and operations processes. Build your cloud in a few simple steps and
operate it with ease at any scale.

> [!NOTE]
>
> For the most up-to-date installation instructions and minimum hardware
> requirements always refer to the official [product documentation][product-documentation].

### Install the openstack snap:

```
sudo snap install openstack
```

### Prepare the machine:

```
sunbeam prepare-node-script --bootstrap | bash -x && newgrp snap_daemon
```

### Bootstrap the cloud:

```
sunbeam cluster bootstrap --accept-defaults --role control,compute,storage
```

### Configure the cloud:

```
sunbeam configure --accept-defaults --openrc demo-openrc
```

### Start using OpenStack:

```
sunbeam launch ubuntu --name test
```

## What is OpenStack?

OpenStack is a collection of open source projects designed to work together to
form the basis of a cloud. It enables organizations to transform their data
centers into modern and robust platforms that resemble the behavior of leading
public clouds, while empowering them to keep full control over their budget and
sovereignty.

## What is Sunbeam?

Sunbeam lowers the barrier to entry for OpenStack and simplifies its deployment
and operations processes. Backed by cloud-native architecture, Sunbeam uses
bottom-up automation and high-level abstraction to make OpenStack accessible to
newcomers and help users get to grips with the platform immediately.

## What is Canonical OpenStack?

Canonical OpenStack (based on Sunbeam) is an enterprise cloud solution that
distills the maturity and comprehensiveness of the upstream OpenStack project
into an award-winning product. Canonical OpenStack gives organizations access
to a broad range of commercial services: from design and delivery to
post-deployment operations and maintenance.

## User / admin documentation

Refer to the official [product documentation][product-documentation] for exact
instructions on getting started.

## Contributing / developer documentation

See the [docs](./docs) directory in this repository.

## Community chat

Get in touch with us through the official [community chat][community-chat].

## Report a bug

Found a bug? Report it, please. See the [OpenStack Snap][snap-openstack] project
on Launchpad.

## Learn more

- [Project website][project-website]
- [Product website][product-website]

[community-chat]: https://matrix.to/#/#openstack-sunbeam:ubuntu.com
[product-documentation]: https://canonical-openstack.readthedocs-hosted.com/en/latest/
[product-website]: https://canonical.com/openstack
[project-website]: https://ubuntu.com/openstack
[snap-openstack]: https://bugs.launchpad.net/snap-openstack
