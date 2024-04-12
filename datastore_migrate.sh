#! /bin/bash

# Example of a script that leverages vmx2xml.py to migrate
# an entire datastore of .VMX VMs and referenced disks

set -x

DATASTORE1=/virt1-share-migration/datastore1
DATASTORE2=/virt1-share-migration/datastore2

for VMX in `find ${DATASTORE1} -name "*.vmx"` ; do
    XML=`echo ${VMX} | sed "s,${DATASTORE1},${DATASTORE2},"`
    vmx2xml.py -d${DATASTORE1}=${DATASTORE2} -c -o ${XML}.xml -f ${VMX}
done
