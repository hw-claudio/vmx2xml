#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# This tool is used to testboot an OS Disk.
#

import sys
import os.path
import argparse

from vmx2xml.log import *
from vmx2xml.adjust import *

program_version: str = "0.1"

def get_options(argc: int, argv: list) -> tuple:
    global log
    use_v2v: int = 1
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='testboot-xml.py',
        usage="%(prog)s [options]\n\n"
        "testboot a libvirt XML definition and the OS-disk image it references.\n\n"
        "By default, adjusts the guestfs by injecting virtio drivers using virt-v2v,\n"
        "virsh defines the VM on the local system and test boots it.\n"
        "Returns 0 exit code on successful boot to network.\n"
    )
    parser.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-V', '--version', action='version', version=program_version)

    parser.add_argument('-f', '--filename', metavar="XMLFILE", action='store', required=True,
                        help='the libvirt XML with the VM definition to test.')
    parser.add_argument('-a', '--skip-adjust', action='store_true', help='skip guest adjustments to run on KVM')
    parser.add_argument('-O', '--overwrite', action='store_true', help='if guest is already defined or running,\n'
                        'destroy it and undefine it, then run the boot test.\n')
    parser.add_argument('-x', '--experimental', action='store_true', help='use experimental guest-injection method (adjust_guestfs.py)')
    parser.add_argument('-X', '--skip-extra', action='store_true', help='ignore any extra non-OS VMDK/qcow2 disks that may be present')

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2
    log_init(args.verbose, args.quiet)

    if (args.experimental):
        use_v2v = 0
    return (args.filename, use_v2v, args.skip_adjust, args.skip_extra)


def main(argc: int, argv: list) -> int:
    (xml_name, use_v2v, skip_adjust, skip_extra) = get_options(argc, argv)
    adjust_version: float = adjust_guestfs_detect_version()

    # check that the file can be opened for reading, close it
    open(xml_name, 'r', encoding="utf-8").close()
    # guestos: str = parse_guestos(d["guestos"])

    #log.debug("%s", disk_ctrls)

    # virsh define
    # virsh start
    # wait
    # ping guest
    # shutdown guest
    # return result
    return 0


sys.exit(main(len(sys.argv), sys.argv))
