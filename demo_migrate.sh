#! /bin/bash
# Used by demo.py to do the final conversion
# args: name, vmxfile, xmlfile, [-d iref:ids=ods] [-n in=out]...
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
vmx2xml.py -v -o ${XML} -f ${VMX} -c -O -x -A -D -T -M -C none $* >${XML}.prg 2>>${XML}.log

RESULT=$?
if test ${RESULT} != "0" ; then
	echo "FAILURE(conv)"
	exit 0
fi
echo "SUCCESS"
exit 0
