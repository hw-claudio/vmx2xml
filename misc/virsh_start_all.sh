#! /bin/bash

for NAME in `virsh list --all --name`; do
    virsh start $NAME
done
