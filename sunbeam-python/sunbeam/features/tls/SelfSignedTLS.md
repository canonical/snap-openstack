# TLS self-signed feature

This feature enables TLS for the selected Traefik endpoints by reusing the
self-signed `certificate-authority` application that is already part of the
OpenStack deployment.

## Installation

To enable the self-signed TLS feature, you need an already bootstrapped
Sunbeam instance. Then run:

```bash
sunbeam enable tls self-signed
```

By default, the self-signed provider integrates with the public Traefik
endpoint only.

To enable TLS for the internal endpoint as well:

```bash
sunbeam enable tls self-signed --endpoint public --endpoint internal
```

To enable TLS for the public, internal, and RGW endpoints:

```bash
sunbeam enable tls self-signed --endpoint public --endpoint internal --endpoint rgw
```

## Configure

No additional certificate configuration is required. The provider signs
Traefik CSRs automatically.

## Contents

This feature reuses the following existing service:
- Self-signed certificates operator: [charm](https://github.com/canonical/self-signed-certificates-operator)

## Removal

To remove the feature, run:

```bash
sunbeam disable tls self-signed
```
