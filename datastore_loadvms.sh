#! /bin/bash

# Example of a script to run from a libvirt host to load vm definitions

DATASTORE=/virt1-share-migration/datastore-libvirt

for XML in `find ${DATASTORE} -name "*.xml"` ; do
    echo "datastore_loadvms.sh: loading ${XML}..."
    virsh define ${XML}
done
