#! /bin/bash

# Example of a script that migrates a single VMX

DATASTORE1=/virt1-share-migration/datastore1
DATASTORE2=/virt1-share-migration/datastore-libvirt
VMIMAGES=/virt1-share-migration/vmimages/floppies
VMX=/virt1-share-migration/datastore1/w2k16esxi/w2k16esxi.vmx

XML=${VMX/${DATASTORE1}/${DATASTORE2}}
XML=${XML/%.vmx/.xml}
echo "datastore_migrate_one.sh: converting $VMX to $XML ..."
vmx2xml.py -s ${VMIMAGES} -o ${XML} -f ${VMX} -d${DATASTORE1}=${DATASTORE2} ${*}
