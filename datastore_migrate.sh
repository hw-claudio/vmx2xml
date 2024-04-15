#! /bin/bash

# Example of a script that leverages vmx2xml.py to migrate
# an entire datastore of .VMX VMs and referenced disks

DATASTORE1=/virt1-share-migration/datastore1
DATASTORE2=/virt1-share-migration/datastore-libvirt
VMIMAGES=/virt1-share-migration/vmimages/floppies

for VMX in `find ${DATASTORE1} -name "*.vmx"` ; do
    XML=${VMX/${DATASTORE1}/${DATASTORE2}}
    XML=${XML/%.vmx/.xml}
    echo "datastore_migrate.sh: converting $VMX to $XML..."
    vmx2xml.py -s ${VMIMAGES} -o ${XML} -f ${VMX} -d${DATASTORE1}=${DATASTORE2} ${*}
done
