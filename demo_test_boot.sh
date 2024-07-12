#! /bin/bash
# Used by demo.py to launch the boot test after successful test conversion.
# args: name, xmlfile
IFS=$'\n'

set -x
NAME=$1
XML=$2
shift 2

# for the log and progress
mkdir -p `dirname ${XML}`
echo "testboot_xml.py" >> ${XML}.log
echo "===============" >> ${XML}.log
# add -k to keep the vm around for debugging purposes
testboot_xml.py -v -v -A -f ${XML} -t 90 -O 2>>${XML}.log

RESULT=$?
if test ${RESULT} = "0" ; then
	echo "SUCCESS"
	exit 0
fi
if test ${RESULT} = "2" ; then
    echo "FAILURE(boot)"
	exit 0
fi
if test ${RESULT} = "1" ; then
	echo "FAILURE(script)"
	exit 0
fi
