#! /bin/bash

for NAME in `virsh pool-list --all --name`; do
	virsh pool-destroy $NAME
    virsh pool-undefine $NAME
done
