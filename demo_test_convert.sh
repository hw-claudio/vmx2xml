#! /bin/bash
# Used by demo.py to do the initial test conversion before the test boot.
# args: name, vmxfile, xmlfile, [-d iref:ids=ods]...
IFS=$'\n'

set -x
NAME=$1
VMX=$2
XML=$3
shift 3

# for the log and progress
mkdir -p `dirname ${XML}`

echo "vmx2xml.py" > ${XML}.log
echo "==========" >> ${XML}.log
vmx2xml.py -n 'name:*=network=default' -n 'type:*=network=default' -v -v -o ${XML} -f ${VMX} -c -O -x -A -T -C none -X $* >${XML}.prg 2>>${XML}.log

RESULT=$?
if test ${RESULT} != "0" ; then
	echo "FAILURE(conv)"
	exit 0
fi
echo "SUCCESS"
exit 0
