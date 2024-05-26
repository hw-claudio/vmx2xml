#! /bin/bash
# Used by demo.py to call and report test conversion and boot results
# args: name, vmxfile, targetds

NAME=$1
VMX=$2
DS2=$3

DS1=`dirname ${VMX}`
DS1=`dirname ${DS1}`
XML=${VMX/${DS1}/${DS2}}
DS1NAME=`basname ${DS1}`

# Maybe not necessary, we will rely on default mappings
# DS_MAP12=/vmfs/volumes/datastore1,${DS1}=${DS2}
vmx2xml.py -q -q -o ${XML} -f ${VMX} -c -O -A -D -X ${*}
RESULT=$?
if ${RESULT} != "0" ; then
	echo "FAILURE CONVERSION"
	exit 0
fi

testboot_xml.py -q -q -f ${XML} -t 40
if ${RESULT} = "0" ; then
	echo "SUCCESS ${VMX}"
	exit 0
fi
if ${RESULT} = "2" ; then
    echo "FAILURE BOOT"
	exit 0
fi
if ${RESULT} = "1" ; then
	echo "FAILURE SCRIPT"
	exit 0
fi
