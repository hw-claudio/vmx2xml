#! /bin/bash

for NAME in `virsh list --all --name`; do
	virsh destroy $NAME
    virsh undefine $NAME --nvram
done
