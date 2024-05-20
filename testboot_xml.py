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
import time

from vmx2xml.log import *
from vmx2xml.img import *
from vmx2xml.adjust import *
from vmx2xml.inspector import *
from vmx2xml.stopwatch import *
from vmx2xml.detectv import *

program_version: str = "0.1"

def detect_virsh_version() -> float:
    return detectv([ "virsh", "--version" ], r"^(\d+\.\d+)", True)


def detect_virt_xml_version() -> float:
    return detectv([ "virt-xml", "--version" ], r"^(\d+\.\d+)", True)


def virsh(params: list, check: bool) -> str:
    s: str; e: str
    args: list = [ "virsh" ]
    args.extend(params)
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    except Exception as exp:
        log.critical("virsh: exception running command: %s: \n%s", args, exp)
        sys.exit(1)
    (s, e) = p.communicate()
    if (p.returncode != 0 and check):
        log.critical("virsh: failure detected in command: %s: \n%s", args, e)
        sys.exit(1)
    return s


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
    log.debug("%s", s)


def ip_neigh_show() -> str:
    s: str; e: str
    args: list = [ "ip", "neigh", "show" ]
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("ip NOT FOUND")
        sys.exit(1)
    (s, e) = p.communicate()
    if (p.returncode != 0):
        log.critical("failure detected in %s: \n%s", args, e)
        sys.exit(1)
    log.debug("%s", s)
    return s


def domain_exists(domainname: str) -> bool:
    # get all existing domain names
    out: str = virsh(["list", "--all", "--name"], True)
    domainname_m = re.search(f"^{domainname}$", out, flags=re.MULTILINE)
    if (domainname_m):
        return True
    return False


def domain_obliterate(domainname: str) -> None:
    virsh(["destroy", domainname], False)
    virsh(["undefine", domainname], False)


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
    parser.add_argument('-t', '--timeout', metavar="SECONDS", action='store', default=60,
                        help='timeout to detect a boot success. Use 0 to never timeout (for debugging)')

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
    timeout: int = int(args.timeout)
    return (args.filename, args.overwrite, use_v2v, args.skip_adjust, timeout)


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
            log.critical("%s: referenced disks need to be .qcow2 or .raw", domainname)
            sys.exit(1)
        ext = ext[1:]
        tmp = img_qemu_create_overlay(source, ext)
        log.info("OVERLAY %s => %s", source, tmp.name)
        if not (skip_adjust):
            log.info("ADJUST %s", tmp.name)
            if (use_v2v == 1):
                adjust_guestfs(tmp.name, False, "v2v" if (use_v2v == 1) else "x")
        log.info("DISK REF %s", tmp.name)
        virt_xml(domainname, ["--edit", str(i + 1), "--disk", f"path={tmp.name}"])
        overlays.append(tmp)
    return overlays


# get (rx, tx) counters
def testboot_net_get_rx_tx(domainname, mac) -> tuple:
    rx: int = -1; tx: int = -1
    out: str = virsh(["domifstat", domainname, mac], True)
    m = re.search(r"^.*rx_packets (\d+)\s*$", out, flags=re.MULTILINE)
    if (not m):
        log.critical("%s: failed to parse rx_packets from domifstat output %s", domainname, out)
        sys.exit(1)
    rx = int(m.group(1))
    m = re.search(r"^.*tx_packets (\d+)\s*$", out, flags=re.MULTILINE)
    if (not m):
        log.critical("%s: failed to parse tx_packets from domifstat output %s", domainname, out)
        sys.exit(1)
    tx = int(m.group(1))
    return (rx, tx)


# check that either the tx, or the rx counters are zero when we start
# ie the VM is not generating any traffic
def testboot_net_is_zero(domainname: str, macs: list) -> bool:
    for mac in macs:
        (rx, tx) = testboot_net_get_rx_tx(domainname, mac)
        if (rx != 0 and tx != 0):
            return False
    return True


def testboot_net(domainname: str, macs: list) -> bool:
    for mac in macs:
        (rx, tx) = testboot_net_get_rx_tx(domainname, mac)
        # test for successful network activity (both tx and rx packets) on any interface
        if (rx > 0 and tx > 0):
            return True
    return False


def find_macs(domainname: str) -> list:
    out: str = virsh(["dumpxml", domainname], True)
    macs = re.findall(r"^.*mac address.*(\w\w:\w\w:\w\w:\w\w:\w\w:\w\w).*$", out, flags=re.MULTILINE)
    if (len(macs) < 1):
        log.critical("%s: could not parse mac address from %s", domainname, out)
        sys.exit(1)
    return macs


def testboot_domain(domainname: str, use_v2v: int, skip_adjust: bool, timeout: int) -> bool:
    list_str: str = virsh(["domblklist", "--details", domainname], True)
    lines: list = list_str.splitlines()
    lines.pop(0)                #  Target   Source
    lines.pop(0)                # --------------------------------------
    if (lines[-1] == ""):
        lines.pop()             # the last line of the output seems to be empty. If that is the case, remove it.

    os_disks: list = []         # list of interesting (i, source) tuples of OS disks to overlay and adjust
    extra_disks: list = []      # list of non-interesting i indices of extra disks to remove

    for i in range(0, len(lines)):
        line: str = lines[i]
        m = re.match(r"^\s*(\S+)\s*(\S+)\s*(\S+)\s*(\S+)\s*$", line)
        if not (m):
            log.warning("%s: domblklist line '%s' not matching expected pattern", domainname, line)
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
        log.critical("%s: no OS disks found, nothing to boot-test", domainname)
        sys.exit(1)

    overlays: list = overlay_adjust_disks(domainname, os_disks, use_v2v, skip_adjust)

    if (len(extra_disks) >= 1):
        remove_disks(domainname, extra_disks)

    # start the domain.
    # TODO: add a --network option for the sandbox
    virsh(["start", domainname, "--paused"], True)

    result: bool = False
    macs = find_macs(domainname)
    # to avoid the race between virsh start and when we check for zero, we start --paused"
    if not (testboot_net_is_zero(domainname, macs)):
        log.critical("%s: net interface tx, rx are not zero at boot time", domainname)
        sys.exit(1)
    virsh(["resume", domainname], True)

    stopwatch_start()
    while (timeout <= 0 or stopwatch_elapsed() < timeout):
        time.sleep(1)
        if (testboot_net(domainname, macs)):
            result = True
            log.info("%s: network activity detected after %s seconds", domainname, stopwatch_elapsed())
            break
    virsh(["destroy", domainname], False)
    virsh(["undefine", domainname], False)
    return result


def main(argc: int, argv: list) -> int:
    (xml_name, overwrite, use_v2v, skip_adjust, timeout) = get_options(argc, argv)
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
            log.warning("domain %s already defined, overwriting", domainname)
            domain_obliterate(domainname)
        else:
            log.warning("domain %s already exists, skipping", domainname)
            sys.exit(0)

    virsh(["define", xml_name], True)
    if (testboot_domain(domainname, use_v2v, skip_adjust, timeout)):
        log.info("domain %s testboot report: SUCCESS", domainname)
        return 0
    else:
        log.warning("domain %s testboot report: FAILURE", domainname)
        return 2                # use 2 to distinguish from a runtime script error


sys.exit(main(len(sys.argv), sys.argv))
