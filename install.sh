#! /bin/bash

FILES="vmx2xml.py vmx2xml/ adjust_guestfs.py testboot_xml.py \
datastore_migrate_one.sh datastore_migrate.sh \
datastore_migrate_test_one.sh"

set -x
scp -r ${FILES} ${1}:/usr/local/bin/
