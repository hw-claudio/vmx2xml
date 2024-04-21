#! /bin/bash
# Example of a script that migrates a single VMX VM.

# Here we map the VMWare Datastore /vmfs/volumes/datastore1 as appears in the VMX files
# to the directory it will be mounted on the conversion host (separated by comma: ','),
# /100G/datastore1.
#
# This will be converted to the destination libvirt datastore (separated by equal: '='),
# ie /100G/datastore-libvirt.
#
DS1=/100G/datastore1
DS2=/100G/datastore-libvirt
DS_MAP12=/vmfs/volumes/datastore1,${DS1}=${DS2}

# In this example we have an external independent persistent disk of ~100G, without an OS,
# that is used to store data.

DS3=/100G/datastore-independent1
DS4=/100G/datastore-libvirt-independent/
DS_MAP34=/vmfs/volumes/7e06e1f8-f272f9cd/datastore-volumes,${DS3}=${DS4}

# Here we have a shared directory mapping that does not need any conversion, since it's just
# floppies, ISOs etc that can stay there.

VMIMAGES=/vmimages,/100G/vmimages

# This is the VMX definition we want to convert. It is in DS1.
VMX=${DS1}/15sp6bios/15sp6bios.vmx

# We want it converted and copied into DS2.
XML=${VMX/${DS1}/${DS2}}

# This is the XML libvirt definition we want to convert to
XML=/100G/datastore-libvirt/15sp6bios/15sp6bios.xml

echo "datastore_migrate_one.sh: converting $VMX to $XML ..."
vmx2xml.py -o ${XML} -f ${VMX} -d ${VMIMAGES} -d ${DS_MAP12} -d ${DS_MAP34} ${*}
