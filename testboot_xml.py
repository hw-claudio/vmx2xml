#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Requires virt-xml and virsh
#
# This tool is used to testboot an OS Disk.

import sys
import os.path
import re
import argparse

from vmx2xml.log import *
from vmx2xml.adjust import *
from vmx2xml.inspector import *
from vmx2xml.img import *

program_version: str = "0.1"

def detect_virsh_version() -> float:
    s: str = ""
    args: list = [ "virsh", "--version" ]

    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("virsh NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect virsh version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("virsh: detected version %s", v)
    return v


def virsh(params: list, check: bool) -> str:
    s: str; e: str
    args: list = [ "virsh" ]
    args.extend(params)
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("virsh NOT FOUND")
        sys.exit(1)
    (s, e) = p.communicate()
    if (p.returncode != 0 and check):
        log.critical("failure detected in %s: \n%s", args, e)
        sys.exit(1)
    return s


def detect_virt_xml_version() -> float:
    s: str = ""
    args: list = [ "virt-xml", "--version" ]

    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("virt-xml NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect virt-xml version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("virt-xml: detected version %s", v)
    return v


def virt_xml(domain: str, params: list) -> None:
    s: str; e: str
    args: list = [ "virt-xml", domain ]
    args.extend(params)
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("virt-xml NOT FOUND")
        sys.exit(1)
    (s, e) = p.communicate()
    if (p.returncode != 0):
        log.critical("failure detected in %s: \n%s", args, e)
        sys.exit(1)


def domain_exists(domainname: str) -> bool:
    # get all existing domain names
    out: str = virsh(["list", "--all", "--name"], True)
    domainname_m = re.search(f"^{domainname}$", out, flags=re.MULTILINE)
    if (domainname_m):
        return True
    return False


def domain_obliterate(domainname: str) -> None:
    out: str = virsh(["destroy", domainname], False)
    out = virsh(["undefine", domainname], False)


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
    return (args.filename, args.overwrite, use_v2v, args.skip_adjust)


def remove_disks(domainname: str, extra_disks: list) -> None:
    args: list = []
    for i in extra_disks:
        args.extend(["--remove-device", "--disk", str(i + 1)])
    virt_xml(domainname, args)


def overlay_adjust_disks(domainname: str, os_disks: list, use_v2v: int, skip_adjust: bool) -> list:
    args: list = []
    overlays: list = []
    for disk in os_disks:
        (i, source) = disk
        (_, ext) = os.path.splitext(source)
        if (ext != ".raw" and ext != ".qcow2"):
            log.critical("referenced disks need to be .qcow2 or .raw")
            sys.exit(1)
        ext = ext[1:]
        tmp = img_qemu_create_overlay(source, ext)
        log.info("OVERLAY %s => %s", source, tmp.name)
        if not (skip_adjust):
            log.info("ADJUST %s", tmp.name)
            if (use_v2v == 1):
                img_v2v_adjust(tmp.name)
            else:
                adjust_guestfs(tmp.name, False)
        log.info("DISK REF %s", tmp.name)
        virt_xml(domainname, ["--edit", str(i + 1), "--disk", f"path={tmp.name}"])
        overlays.append(tmp)
    return overlays


def process_disks(domainname: str, use_v2v: int, skip_adjust: bool) -> None:
    list_str: str = virsh(["domblklist", "--details", domainname], True)
    log.debug(list_str)

    lines: list = list_str.splitlines()
    lines.pop(0)                #  Target   Source
    lines.pop(0)                # --------------------------------------

    os_disks: list = []         # list of interesting (i, source) tuples of OS disks to overlay and adjust
    extra_disks: list = []      # list of non-interesting i indices of extra disks to remove

    for i in range(0, len(lines)):
        line: str = lines[i]
        m = re.match(r"^\s*(\S+)\s*(\S+)\s*(\S+)\s*(\S+)\s*$", line)
        if not (m):
            log.warning("domblklist line %s not matching expected pattern")
            continue
        type_str: str = m.group(1)
        device: str = m.group(2)
        target: str = m.group(3)
        source: str = m.group(4)
        log.info("DISK type:%s device:%s target:%s source:%s", type_str, device, target, source)
        if (type_str == "file" and device == "disk" and (source.endswith(".qcow2") or source.endswith(".raw"))):
            osd: dict = inspector_inspect(source)
            if (osd["name"]):
                log.debug("DISK is OS: %s", osd)
                os_disks.append((i, source))
                continue
        log.debug("DISK is an extra, non-OS disk")
        extra_disks.append(i)   # not interesting, mark it for removal

    if (len(os_disks) < 1):
        log.critical("no OS disks found, nothing to boot-test")
        sys.exit(1)

    overlays: list = overlay_adjust_disks(domainname, os_disks, use_v2v, skip_adjust)

    if (len(extra_disks) >= 1):
        remove_disks(domainname, extra_disks)

    # testboot
    # set --network ? for the sandbox? how?
    out: str = virsh(["start", domainname], True)
    log.debug(out)


def main(argc: int, argv: list) -> int:
    (xml_name, overwrite, use_v2v, skip_adjust) = get_options(argc, argv)
    adjust_version: float = adjust_guestfs_detect_version()
    virsh_version: float = detect_virsh_version()
    virt_xml_version: float = detect_virt_xml_version()

    # check the input file name and whether it can be opened for reading
    if not (xml_name.endswith(".xml")):
        log.critical("invalid xml name %s, does not end in .xml", xml_name)
        sys.exit(1)
    open(xml_name, 'r', encoding="utf-8").close()
    (domainname, n) = re.subn(r"\.xml$", "", os.path.basename(xml_name), count=1, flags=re.IGNORECASE)
    if (n != 1):
        log.critical("invalid xml name %s, does not end in .xml", xml_name)
        sys.exit(1)
    if (domain_exists(domainname)):
        if (overwrite):
            log.warning("domain %s already defined, overwriting")
            domain_obliterate(domainname)
        else:
            log.warning("domain %s already exists, skipping")
            sys.exit(0)

    out: str = virsh(["define", xml_name], True)
    log.debug(out)

    process_disks(domainname, use_v2v, skip_adjust)
    return 0


sys.exit(main(len(sys.argv), sys.argv))
