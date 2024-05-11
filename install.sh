#! /bin/bash

set -x
scp datastore_migrate_test_one.sh guestfs_adjust.py datastore_migrate_one.sh datastore_migrate.sh vmx2xml.py \
	${1}:/usr/local/bin/
