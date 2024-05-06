#! /bin/bash
# Example of a script that test-migrates a single VMX VM.
# See datastore_migrate_one.sh for more explanations on the datastore mapping.
#
# We convert from the original datastore1 to local storage, without any guest adjustment,
# then we create an overlay where we do the actual adjustment and test-boot it.
#
# Non-OS disks should be ignored (ie not converted).
#
# If unsuccessful, we log an error and exit with error,
# Otherwise we exit with success.
#
DS1=/153-RAID0/datastore1
DS2=/vm_images/local
DS_MAP12=/vmfs/volumes/datastore1,${DS1}=${DS2}

# This mapping is useful to find the Extra disk, but we don't do anything with it.
DS3=/153-RAID0/datastore-independent1
DS_MAP3=/vmfs/volumes/7e06e1f8-f272f9cd/datastore-volumes,${DS3}=/tmp/notused

# Here we have a shared directory mapping that does not need any conversion, since it's just
# floppies, ISOs etc that can stay there.

VMIMAGES=/vmimages,/153-RAID0/vmimages

# This is the VMX definition we want to convert. It is in DS1.
VMX=${DS1}/15sp6bios/15sp6bios.vmx

# We want it converted and copied into DS2.
XML=${VMX/${DS1}/${DS2}}

# This is the XML libvirt definition we want to convert to
XML=/vm_images/local/15sp6bios/15sp6bios.xml

echo "datastore_migrate_test_one.sh: test-converting $VMX to $XML ..."
vmx2xml.py -o ${XML} -f ${VMX} -d ${VMIMAGES} -d ${DS_MAP12} -d ${DS_MAP3} -c -O -a -X ${*}
