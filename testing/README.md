# Testflinger Testing

## Local Development/Testing

To run the testing, it's possible to use the `./testing/local-testflinger.sh` script.

Usage example:

1. Install the testflinger-cli snap: `sudo snap install testflinger-cli`.
2. Make sure there is a copy of the openstack snap at the toplevel of the git
   repo. Use `snap download` or `snapcraft pack`.

``` sh
snap download --channel 2024.1/edge openstack
```

``` sh
snapcraft pack --use-lxd
```

3. Run `./testing/local-testflinger.sh`.

## TODO

* [ ] Expose a knob to turn on/off the log level of terragrunt/terraform.


## Known Issues

* When a libvirt instance does PXE boot, there could be situations where it
  doesn't boot and it just times out, making the whole deployment timeout or
  fail when terraform's apply times out.
