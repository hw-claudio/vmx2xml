#! /bin/bash
# Usage: datastore_find_external_disks.sh FOLDER ..

for DIRNAME in $* ; do
	VMXS=`find ${DIRNAME} -name "*.vmx"`
	fgrep -i ".filename" ${VMXS} | fgrep \"/ | sed 's,^.*"\(.*\)".*$,\1,'
done
