#! /bin/bash

for NAME in `virsh list --all --name`; do
    virsh shutdown --mode agent $NAME || virsh shutdown --mode acpi $NAME
done

sleep 10

for NAME in `virsh list --all --name`; do
    virsh destroy $NAME
done
