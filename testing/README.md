# Testflinger Testing

## TODO

* [ ] Expose a knob to turn on/off the log level of terragrunt/terraform.


## Known Issues

* When a libvirt instance does PXE boot, there could be situations where it
  doesn't boot and it just times out, making the whole deployment timeout or
  fail when terraform's apply times out.
