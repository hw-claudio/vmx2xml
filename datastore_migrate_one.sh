#! /bin/bash
# Example of a script that migrates a single VMX VM.

# Here we map the VMWare Datastore /vmfs/volumes/datastore1 as appears in the VMX files
# to the directory it will be mounted on the conversion host (separated by comma: ','),
# /100G/datastore1.
#
# This will be converted to the destination libvirt datastore (separated by equal: '='),
# ie /100G/datastore-libvirt.
#

DS_MAP1=/vmfs/volumes/datastore1,/100G/datastore1=/100G/datastore-libvirt

# In this example we have an external independent persistent disk of ~100G, without an OS,
# that is used to store data.

DS_MAP2=/vmfs/volumes/7e06e1f8-f272f9cd/datastore-volumes,/100G/datastore-independent1=/100G/datastore-libvirt-independent/

# Here we have a shared directory mapping that does not need any conversion, since it's just
# floppies, maybe ISOs that can stay there.

VMIMAGES=/vmimages,/100G/vmimages

# This is the VMX definition we want to convert
VMX=/100G/datastore1/15sp6bios/15sp6bios.vmx

# This is the XML libvirt definition we want to convert to
XML=/100G/datastore-libvirt/15sp6bios/15sp6bios.xml

echo "datastore_migrate_one.sh: converting $VMX to $XML ..."
vmx2xml.py -o ${XML} -f ${VMX} -d ${VMIMAGES} -d ${DS_MAP1} -d ${DS_MAP2} ${*}
