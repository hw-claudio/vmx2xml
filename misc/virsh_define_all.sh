#! /bin/bash

DATASTORE=$1

for XML in `find ${DATASTORE} -name "*.xml"` ; do
    virsh define $XML
done
