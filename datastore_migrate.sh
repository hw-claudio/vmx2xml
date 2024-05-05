#! /bin/bash

# Example of a script that leverages vmx2xml.py to migrate
# an entire datastore of .VMX VMs and referenced disks.
# See datastore_migrate_one.sh for more comments.

DS1=/153-RAID0/datastore1
DS2=/154-RAID0/datastore-libvirt
DS_MAP12=/vmfs/volumes/datastore1,${DS1}=${DS2}

DS3=/153-RAID0/datastore-independent1
DS4=/154-RAID0/datastore-libvirt-independent
DS_MAP34=/vmfs/volumes/7e06e1f8-f272f9cd/datastore-volumes,${DS3}=${DS4}

VMIMAGES=/vmimages,/153-RAID0/vmimages

# In this simple loop we assume that we want to convert all VMX in DS1 into DS2.
# For any external disk referenced by VMs in DS3, we want to put those disks in DS4.

for VMX in `find ${DS1} -name "*.vmx"` ; do
    XML=${VMX/${DS1}/${DS2}}
    XML=${XML/%.vmx/.xml}
    echo "datastore_migrate.sh: converting $VMX to $XML ..."
    vmx2xml.py -o ${XML} -f ${VMX} -d ${VMIMAGES} -d ${DS_MAP12} -d ${DS_MAP34} ${*}
done
