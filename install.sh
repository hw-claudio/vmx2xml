#! /bin/bash

set -x
scp -r datastore_migrate_test_one.sh guestfs_adjust.py datastore_migrate_one.sh datastore_migrate.sh vmx2xml.py vmx2xml/ \
	${1}:/usr/local/bin/
