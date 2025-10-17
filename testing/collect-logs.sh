#!/bin/bash -ux

## Collect relevant files (if possible)
sudo mkdir /tmp/sosreport/
sudo sosreport -a --batch --label hypervisor --all-logs --tmp-dir=/tmp/sosreport/
sudo mv /tmp/sosreport/* $ARTIFACTS_DIR
ssh -i ~/.ssh/passwordless ubuntu@172.16.1.2 "sudo mkdir /tmp/sosreport; sudo sosreport -a --batch --label maas-controller --all-logs --tmp-dir=/tmp/sosreport/; sudo chmod +r /tmp/sosreport/"
scp -i ~/.ssh/passwordless ubuntu@172.16.1.2:"/tmp/sosreport/*" $ARTIFACTS_DIR
sudo chmod +r -R $ARTIFACTS_DIR
