#! /bin/bash
# Usage: datastore_find_networks.sh FOLDER ..

for DIRNAME in $* ; do
	VMXS=`find ${DIRNAME} -name "*.vmx"`
	NAMES=`fgrep -i ".networkname" ${VMXS} | sed 's,^.*"\(.*\)".*$,\1,'`
	for NAME in ${NAMES} ; do
		echo "name:${NAME}"
	done
	TYPES=`fgrep -i ".connectiontype" ${VMXS} | sed 's,^.*"\(.*\)".*$,\1,'`
	for TYPE in ${TYPES} ; do
		echo "type:${TYPE}"
	done
done
