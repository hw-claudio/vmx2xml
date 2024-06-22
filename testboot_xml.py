#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# Requires virt-xml and virsh
# This tool is used to testboot an OS Disk.

import sys
import os.path
import re
import argparse
import time

from vmx2xml.log import log, log_init
from vmx2xml.img import img_qemu_create_overlay
from vmx2xml.adjust import adjust_guestfs_detect_version, adjust_guestfs
from vmx2xml.inspector import inspector_inspect
from vmx2xml.stopwatch import stopwatch_start, stopwatch_elapsed
from vmx2xml.runcmd import runcmd_detectv, runcmd

program_version: str = "0.1"

def detect_virsh_version() -> float:
    return runcmd_detectv(["virsh", "--version"], r"^(\d+\.\d+)", True)


def detect_virt_xml_version() -> float:
    return runcmd_detectv(["virt-xml", "--version"], r"^(\d+\.\d+)", True)


def detect_arping_version() -> float:
    return runcmd_detectv(["arping", "-V"], r"^arping.*(\d+)$", True)


def virsh(params: list, check: bool) -> str:
    args: list = ["virsh"]
    args.extend(params)
    return runcmd(args, check)


def virt_xml(domain: str, params: list) -> None:
    args: list = ["virt-xml", domain]
    args.extend(params)
    runcmd(args, True)


def domain_exists(domainname: str) -> bool:
    # get all existing domain names
    out: str = virsh(["list", "--all", "--name"], True)
    domainname_m = re.search(f"^{domainname}$", out, flags=re.MULTILINE)
    if (domainname_m):
        return True
    return False


def domain_obliterate(domainname: str) -> None:
    virsh(["destroy", domainname], False)
    virsh(["undefine", "--nvram", domainname], False)


def network_available(network: str) -> bool:
    out: str = virsh(["net-info", "--network", network], False)
    if not (out):
        log.critical("Network not found: no network with matching name '%s'", network)
        return False
    else:
        network_m = re.search(f"Active:\s+yes", out, flags = re.MULTILINE)
        if (network_m):
            return True
        log.critical("Network '%s' is not active", network)
        return False


def get_options(argc: int, argv: list) -> tuple:
    global log
    adj_modes: list = ["none", "v2v", "x"]
    adj_mode: str = "v2v"
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
    parser.add_argument('-O', '--overwrite', action='store_true', help='if guest is already defined or running,\n'
                        'destroy it and undefine it, then run the boot test.\n')
    parser.add_argument('-f', '--filename', metavar="XMLFILE", action='store', required=True,
                        help='the libvirt XML with the VM definition to test.')
    parser.add_argument('-a', '--skip-adjust', action='store_true', help='skip guest adjustments to run on KVM')
    parser.add_argument('-A', '--x-adjust', action='store_true', help='experimental minimal guest adjustments.')
    parser.add_argument('-2', '--layer2', action='store_true', help='perform only a layer 2 net transmission test.')
    parser.add_argument('-t', '--timeout', metavar="SECONDS", action='store', default=60,
                        help='timeout to detect a boot success. Use 0 to never timeout (for debugging)')
    parser.add_argument('-k', '--keep', action='store_true', help='keep running after testboot success (debug only)')
    parser.add_argument('-m', '--sandbox', metavar="NETNAME", action='store', default='isolated',
                        help='libvirt network to replace all networks during the test (default "isolated")')

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2

    log_init(args.verbose, args.quiet)

    if (args.x_adjust and args.skip_adjust):
        log.critical("cannot specify both --skip-adjust and --x-adjust at the same time.")
        sys.exit(1)
    if (args.x_adjust):
        adj_mode = "x"
    elif (args.skip_adjust):
        adj_mode = "none"

    timeout: int = int(args.timeout)
    return (args.filename, args.overwrite, adj_mode, timeout, args.layer2, args.keep, args.sandbox)


def remove_disks(domainname: str, extra_disks: list) -> None:
    args: list = []
    for i in extra_disks:
        args.extend(["--remove-device", "--disk", str(i + 1)])
    virt_xml(domainname, args)


def modify_networks(domainname: str, network: str) -> None:
    virt_xml(domainname, ["--edit", "all", "--network", f"network={network}"])


def overlay_adjust_disks(domainname: str, os_disks: list, adj_mode: str) -> list:
    overlays: list = []
    for disk in os_disks:
        (i, source) = disk
        (_, ext) = os.path.splitext(source)
        if (ext != ".raw" and ext != ".qcow2"):
            log.critical("%s: referenced disks need to be .qcow2 or .raw", domainname)
            sys.exit(1)
        ext = ext[1:]
        tmp = img_qemu_create_overlay(source, ext)
        log.info("[OVERLAY] %s => %s", source, tmp.name)
        if (adj_mode != "none"):
            log.info("[ADJUST] %s", tmp.name)
            # we only need to inject the drivers here, no need to trim the image as we run it directly
            adj_actions = {"drivers": True, "trim": False}
            adjust_guestfs(tmp.name, False, adj_mode, adj_actions)
        log.info("[DISK] REF %s", tmp.name)
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


def testboot_net_layer2(domainname: str, macs: list) -> bool:
    for mac in macs:
        (rx, tx) = testboot_net_get_rx_tx(domainname, mac)
        # test for successful network activity (both tx and rx packets) on any interface
        if (rx > 0 and tx > 0):
            return True
    return False


def testboot_net_get_ip(network: str, mac: str) -> str:
    s: str = virsh(["net-dhcp-leases", network, mac], True)
    # 2024-05-20 14:59:08  52:54:00:8c:25:ef ipv4 192.168.2.94/24 xxx
    m = re.search(fr"^.*{mac}.*\s(\d+\.\d+\.\d+\.\d+).*$", s, flags=re.MULTILINE | re.IGNORECASE)
    if not (m):
        return ""
    return m.group(1)


def testboot_net_arping(ip: str, mac: str) -> bool:
    # send two ARP pings, wait at most two seconds total,
    # exit as soon as a reply is received on success.
    # Unicast reply from 192.168.2.94 [52:54:00:8C:25:EF]  1.244ms
    s: str = runcmd(["arping", "-c", "2", "-w", "2", "-f", ip], False)
    if (not s):
        return False

    m = re.search(f"^.*reply.*{ip}.*{mac}.*$", s, flags=re.MULTILINE | re.IGNORECASE)
    if (m):
        return True
    return False


def testboot_net_layer3(network: str, macs: list) -> bool:
    for mac in macs:
        ip: str = testboot_net_get_ip(network, mac)
        if (ip and testboot_net_arping(ip, mac)):
            return True
    return False


def testboot_net(domainname: str, network: str, macs: list, layer2: bool) -> bool:
    if (layer2):
        return testboot_net_layer2(domainname, macs)
    else:
        return testboot_net_layer3(network, macs)


def find_macs(domainname: str) -> list:
    out: str = virsh(["dumpxml", domainname], True)
    macs = re.findall(r"^.*mac address.*(\w\w:\w\w:\w\w:\w\w:\w\w:\w\w).*$", out, flags=re.MULTILINE)
    if (len(macs) < 1):
        log.critical("%s: could not parse mac address from %s", domainname, out)
        sys.exit(1)
    return macs


def find_disks(domainname: str) -> tuple:
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
        log.info("[DISK] type:%s device:%s target:%s source:%s", type_str, device, target, source)
        if (type_str == "file" and device == "disk" and (source.endswith(".qcow2") or source.endswith(".raw"))):
            osd: dict = inspector_inspect(source)
            if (osd["name"]):
                log.debug("[DISK] is OS: %s", osd)
                os_disks.append((i, source))
                continue
        log.debug("[DISK] is an extra, non-OS disk")
        extra_disks.append(i)   # not interesting, mark it for removal
    return (os_disks, extra_disks)


def testboot_domain(domainname: str, adj_mode: str, timeout: int, layer2: bool, sandbox: str) -> bool:
    (os_disks, extra_disks) = find_disks(domainname)

    if (len(os_disks) < 1):
        log.critical("%s: no OS disks found, nothing to boot-test", domainname)
        sys.exit(1)

    # XXX the overlays return value appears unused but there is a catch!
    # It needs to be here in this scope, so that the temporary files are not deleted before we run the test!
    _ = overlay_adjust_disks(domainname, os_disks, adj_mode)

    if (len(extra_disks) >= 1):
        remove_disks(domainname, extra_disks)

    macs = find_macs(domainname)
    modify_networks(domainname, sandbox)

    # start the domain.
    virsh(["start", domainname, "--paused"], True) # start paused to avoid race with is_zero
    result: bool = False
    if (layer2 and not testboot_net_is_zero(domainname, macs)):
        log.critical("%s: net interface not clear at boot time", domainname)
        sys.exit(1)
    virsh(["resume", domainname], True)

    stopwatch_start()
    while (timeout <= 0 or stopwatch_elapsed() < timeout):
        if (testboot_net(domainname, sandbox, macs, layer2)):
            result = True
            log.info("%s: network activity detected after %s seconds", domainname, stopwatch_elapsed())
            break
        time.sleep(1)
    return result


def main(argc: int, argv: list) -> int:
    (xml_name, overwrite, adj_mode, timeout, layer2, keep_running, sandbox) = get_options(argc, argv)
    _ = adjust_guestfs_detect_version()
    _ = detect_virsh_version()
    _ = detect_virt_xml_version()
    _ = detect_arping_version()

    if not (network_available(sandbox)):
        sys.exit(1)

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

    if (testboot_domain(domainname, adj_mode, timeout, layer2, sandbox)):
        log.info("domain %s testboot report: SUCCESS", domainname)
        while (keep_running):
            time.sleep(60)
        domain_obliterate(domainname)
        return 0
    else:
        log.warning("domain %s testboot report: FAILURE", domainname)
        domain_obliterate(domainname)
        return 2                # use 2 to distinguish from a runtime script error


sys.exit(main(len(sys.argv), sys.argv))
