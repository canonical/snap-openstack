#!/bin/bash -ex

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR; echo 'Cleaned up temporary file.'" EXIT
cp -rf ../snap-openstack/ $TMP_DIR/repository
pushd $TMP_DIR
tar --exclude=repository/.tox --exclude=repository/.github/workflows/testflinger/repository.tar.gz --exclude=repository/.git -acf   repository.tar.gz repository/
ls -lh repository.tar.gz
popd
export TESTFLINGER_DIR=$(pwd)/.github/workflows/testflinger/
cp $TMP_DIR/repository.tar.gz $TESTFLINGER_DIR
export OPENSTACK_SNAP_PATH=$(ls openstack_*.snap)
JOB_FILE=$TESTFLINGER_DIR/job.yaml
envsubst '$OPENSTACK_SNAP_PATH' \
            < $TESTFLINGER_DIR/job.yaml.tpl \
            > $JOB_FILE

test -f $JOB_FILE
cd $TESTFLINGER_DIR
testflinger-cli -d submit --poll $JOB_FILE
rm -rf $TMP_DIR
