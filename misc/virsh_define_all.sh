#! /bin/bash

DATASTORE=$*

for XML in `find ${DATASTORE} -name "*.xml"` ; do
    virsh define $XML
done
