#! /bin/bash
# to be called from the previous directory

FILES="vmx2xml.py vmx2xml/ adjust_guestfs.py testboot_xml.py \
datastore_migrate_one.sh datastore_migrate.sh \
datastore_migrate_test_one.sh datastore_migrate_win.sh \
demo.py demo_test_convert.sh demo_test_boot.sh demo_migrate.sh art/ \
datastore_find_external_disks.sh datastore_find_networks.sh \
misc/ "

set -x
scp -r ${FILES} ${1}:/usr/local/bin/
