#! /bin/bash
# Example of a script that migrates a single VMX VM.

# Here we map the VMWare Datastore /vmfs/volumes/datastore1 as appears in the VMX files
# to the directory it will be mounted on the conversion host (separated by comma: ','),
# /153-RAID0/datastore1
#
# This will be converted to the destination libvirt datastore (separated by equal: '='),
# ie /154-RAID0/datastore-libvirt
#
DS1=/153-RAID0/datastore1
DS2=/154-RAID0/datastore-libvirt
DS_MAP12=/vmfs/volumes/datastore1,${DS1}=${DS2}

# In this example we have an external independent persistent disk of ~100G, without an OS,
# that is used to store data.

DS3=/153-RAID0/datastore-independent1
DS4=/154-RAID0/datastore-libvirt-independent
DS_MAP34=/vmfs/volumes/02b1be22-0447d341,${DS3}=${DS4}

# Here we have a shared directory mapping that does not need any conversion, since it's just
# floppies, ISOs etc that can stay there.

VMIMAGES=/vmimages,/153-RAID0/vmimages

# This is the VMX definition we want to convert. It is in DS1.
VMX=${DS1}/15sp6bios/15sp6bios.vmx

# We want it converted and copied into DS2.
XML=${VMX/${DS1}/${DS2}}

# This is the XML libvirt definition we want to convert to
XML=/154-RAID0/datastore-libvirt/15sp6bios/15sp6bios.xml

echo "datastore_migrate_one.sh: converting $VMX to $XML ..."
vmx2xml.py -o ${XML} -f ${VMX} -d ${VMIMAGES} -d ${DS_MAP12} -d ${DS_MAP34} ${*}
