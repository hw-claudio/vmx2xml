#! /bin/bash
# Used by demo.py to call and report test conversion and boot results
# args: name, vmxfile, targetds, [-d iref:ids=ods]...

set -x
NAME=$1
VMX=$2
DS2=$3

shift 3
DS1=`dirname ${VMX}`
DS1=`dirname ${DS1}`
XML=${VMX/${DS1}/${DS2}}
XML=${XML/%.vmx/.xml}
DS1NAME=`basename ${DS1}`

#['demo_testboot_xml.sh', 'sdm_sdmrhnwapp2', '/home/claudio/git/vmx-examples/redhat7and6/sdm_sdmrhnwapp2/sdm_sdmrhnwapp2.vmx', '/vm_testboot', '-d/vmfs/volumes/04707693-68dd9a8b,=']
# for the log
mkdir -p `dirname ${XML}`

echo "vmx2xml.py" > ${XML}.log
echo "==========" >> ${XML}.log
vmx2xml.py -v -o ${XML} -f ${VMX} -c -O -A -D -X $* 2>>${XML}.log

RESULT=$?
if test ${RESULT} != "0" ; then
	echo "FAILURE(conversion)"
	exit 0
fi

echo "testboot_xml.py" >> ${XML}.log
echo "===============" >> ${XML}.log
testboot_xml.py -v -f ${XML} -t 60 -O 2>>${XML}.log

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
