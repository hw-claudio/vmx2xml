#! /bin/bash

# Example of a script that migrates a single VMX

DATASTORE1=/100G/datastore1/
DATASTORE2=/100G/datastore-libvirt/
VMIMAGES=/100G/vmimages/
VMX=${DATASTORE1}/15sp6bios/15sp6bios.vmx

XML=${VMX/${DATASTORE1}/${DATASTORE2}}
XML=${XML/%.vmx/.xml}
echo "datastore_migrate_one.sh: converting $VMX to $XML ..."
vmx2xml.py -s ${VMIMAGES} -o ${XML} -f ${VMX} -d${DATASTORE1}=${DATASTORE2} ${*}
