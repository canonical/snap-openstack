#!/bin/bash -exu

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

pushd $SCRIPT_DIR

# export TERRAGRUNT_LOG_LEVEL=trace
# export TF_LOG=TRACE
terragrunt --non-interactive run-all apply
