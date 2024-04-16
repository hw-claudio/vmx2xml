#! /bin/bash

DATASTORE=/virt1-share-migration/datastore-libvirt/

for XML in `find ${DATASTORE} -name "*.xml"` ; do
    virsh define $XML
done
