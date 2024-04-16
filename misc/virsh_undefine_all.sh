#! /bin/bash

for NAME in `virsh list --all --name`; do
    virsh undefine $NAME --nvram
done
