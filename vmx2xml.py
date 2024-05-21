#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Currently requires virt-install 2.2 and recommends 4.0
# also requires qemu-img, adjust_guestfs.py
#
# This tool is mostly used to configure the xml so that it more closely matches
# the configuration pre-conversion.
#

import sys
import os
import re
import subprocess
import argparse
from os.path import join
from collections import defaultdict
import shutil
import filecmp
import struct

from vmx2xml.log import *
from vmx2xml.numa import *
from vmx2xml.trace import *
from vmx2xml.adjust import *
from vmx2xml.inspector import *
from vmx2xml.img import *
from vmx2xml.stopwatch import *
from vmx2xml.runcmd import *

program_version: str = "0.1"


# translate string using a passed dictionary
def translate(dictionary: defaultdict, s: str) -> str:
    if (s not in dictionary):
        return ""
    return dictionary[s]


def parse_boolean(s: str) -> bool:
    s = s.lower()
    if (s == "true"):
        return True
    else:
        return False


# parse a Reference to a filename in the VMX
def parse_filename_ref(s: str, datastores: dict, translate_disk: bool, raw: bool) -> list:
    # an empty string is valid, not really present.
    if (not s):
        return [None, None]

    basename: str = os.path.basename(s)
    log.info("[DISK] %s => ", basename)

    if (s.startswith("/vmfs/devices")):
        log.error("VM references a local device, this cannot work! Ignoring.")
        return [None, None]

    # find the file referenced by the vmx in the locally reachable filesystem
    paths: list = [None, None]

    # a relative path is relative to the VM directory
    if not (os.path.isabs(s)):
        log.info("looking in datastore '.' %s", datastores["."])
        paths = find_file_ref(basename, datastores["."][0], datastores["."], False)
    if (not all(paths)):
        dirname: str = os.path.dirname(s)
        for ref in datastores:
            # skip special datastores that are considered before and after this loop.
            if (ref == "." or ref == ".."):
                continue
            log.info("looking in datastore %s", datastores[ref])
            sourcedir = datastores[ref][0]
            log.debug(f're.subn("^{ref}", "{sourcedir}", {dirname}, count=1')
            (match, n) = re.subn(f"^{ref}", sourcedir, dirname, count=1)
            if (n == 1):
                log.debug('[MATCH] %s', match)
                paths = find_file_ref(basename, match, datastores[ref], True)
                break
            else:
                log.debug('[NO MATCH]')

    # last fallback is to check this datastore
    if (not all (paths)):
        log.info("looking in datastore '..' %s", datastores[".."])
        paths = find_file_ref(basename, datastores[".."][0], datastores[".."], False)
    if (not all (paths)):
        log.critical("\n%s NOT FOUND, datastores %s", basename, datastores)
        sys.exit(1)

    if (translate_disk):
        to_file_ext: str = img_file_ext(raw)
        (match, is_vmdk) = re.subn(r"\.vmdk$", f".{to_file_ext}", paths[1], count=1, flags=re.IGNORECASE)
        if (is_vmdk == 1):
            paths[1] = match

    log.info("%s", paths)
    return paths


def parse_genid(genid: int, genidx: int) -> str:
    # e9392370-2917-565e-692b-d057f46512d6
    if (genid == 0 and genidx == 0):
        return ""
    packed_num = struct.pack('>q', genid)
    ugenid = struct.unpack('>Q', packed_num)[0]
    packed_num = struct.pack('>q', genidx)
    ugenidx = struct.unpack('>Q', packed_num)[0]
    s: str = f"{ugenidx:016x}{ugenid:016x}"
    # insert the - chars in the proper position
    if (len(s) != 32):
        log.warning(f'malformed GENID: "{s}"')
    result: str = s[0:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:32]
    return result


def parse_vm_affinity(s: str) -> str:
    if (not s or s.lower() == "all"):
        return ""
    # it seems that the vmx affinity string is a valid cpuset string
    return s


def walk_find(sourcepath: str, name: str) -> str:
    for (root, dirs, files) in os.walk(sourcepath, followlinks=True):
        for this in files:
            if (this == name):
                return os.path.join(root, name)
    return ""


# find a file referred to by the VMX file
def find_file_ref(name: str, match: str, datastore: tuple, recurse: bool) -> list:
    sourcepath: str = match
    sourcefile: str = ""
    pathname: str = os.path.join(sourcepath, name)
    if (os.path.exists(pathname)):
        sourcefile = pathname
    elif (recurse):
        sourcefile = walk_find(sourcepath, name)
    if (not sourcefile):
        return [ None, None ]
    log.debug("find_file_ref sourcefile %s sourcepath %s pathname %s", sourcefile, sourcepath, pathname)
    targetfile: str = os.path.join(datastore[1], os.path.relpath(sourcefile, datastore[0]))
    log.debug("find_file_ref targetfile %s", targetfile)
    return [ sourcefile, targetfile ]


def parse_vmx(f, d: defaultdict) -> None:
    while (True):
        line: str = f.readline()
        if (line == ""):        # EOF
            return
        line = line.strip()
        if (line == "" or line[0] == "#" or line[0] == "!"):
            continue            # ignore
        offset: int
        try:
            offset = line.index("=")
        except:
            offset = -1
        if (offset < 0):
            continue            # no =, malformed line
        name : str = line[0:offset]
        name = name.strip().lower()

        value : str = line[offset + 1:]
        value = value.strip()
        value = value.strip('"') # remove enclosing double quotes if any
        d[name] = value


def translate_scsi_controller_model(model: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "":           "buslogic",
        "auto":       "auto",
        "lsilogic":   "lsilogic",
        "lsisas1068": "lsisas1068",
        "pvscsi":     "virtio-scsi"
    })
    return translate(translator, model)


def find_disk_controllers(d: defaultdict, interface: str) -> dict:
    controllers: defaultdict = defaultdict(str)
    for x in range(4):               # from "How Storage Controller Technology Works" VSphere7 docs
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        model: str = ""
        if (interface == "scsi"):    # Only SCSI seems to have virtualdev
            model = translate_scsi_controller_model(d[f"{interface}{x}.virtualdev"])
        controllers[x] = { "x": x, "model": model }
    return controllers


def find_disks(d: defaultdict, datastores: dict, interface: str, controllers: dict, disk_mode: str, raw: bool) -> list:
    disks: list = []
    for x in range(4):
        if (x not in controllers) and (interface != "ide"):  # IDE does not show explicit controllers entries
            continue
        for y in range(30):           # max is from SATA ("How Storage Controller Technology Works" VSphere7)
            if not (parse_boolean(d[f"{interface}{x}:{y}.present"])):
                continue
            if (interface == "ide"):  # insert IDE Controller
                controllers[x] = { "x": x, "model": "" }
            disk: dict = {
                "bus": interface, "x": x, "y": y,
                "device": '', "driver": '',
                "cache": '', "path": [ None, None ] ,
                "os": { "name": '', "osinfo": '' }
            }
            t: str = d[f"{interface}{x}:{y}.devicetype"].lower()
            disk["device"] = "cdrom" if ("cdrom" in t) else "disk"
            disk["path"] = parse_filename_ref(d[f"{interface}{x}:{y}.filename"], datastores, disk_mode != "none", raw)
            #disk["driver"] = "block" if (disk["path"].startswith("/dev/")) else "file"
            disk["driver"] = "file"
            # XXX we never use the actual libvirt/qemu default, writeback?
            disk["cache"] = "writethrough" if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])) else "none"
            if (all(disk["path"]) and disk_mode == "convert" and disk["path"][0].endswith(".vmdk")):
                disk["os"] = inspector_inspect(disk["path"][0])
            disks.append(disk)
    return disks


def find_sound(d: defaultdict) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "default",
        "es1371":  "es1370",
        "hdaudio": "hda",
        "sb16":    "sb16",
    })
    if not (parse_boolean(d["sound.present"])):
        return ""
    if (parse_boolean(d["sound.autodetect"])):
        return "default"
    return translate(translator, d["sound.virtualdev"])


def translate_eth_model(model: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "",            # default empty?
        "vlance": "pcnet", # for old 32bit OSes (win98)
        "e1000": "e1000",  # winxp, linux-2.4.19
        "e1000e": "e1000e",  # windows 8, server 2012
        "vmxnet": "virtio-net",   # convert PV to PV
        "vmxnet2": "virtio-net",  # convert PV to PV
        "vmxnet3": "virtio-net"   # convert PV to PV
    })
    return translate(translator, model)


def parse_eth_type(eth_type: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "",
        "bridged": "bridged",
        "vmnet0": "bridged",
        "hostonly": "hostonly",
        "vmnet1": "hostonly",
        "nat": "nat",
        "vmnet8": "nat",
    })
    return translate(translator, eth_type)


# default type mapping
def translate_eth_type(eth_type: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "",
        "bridged": "bridge",
        "hostonly": "network=isolated",
        "nat": "network=default",
    })
    return translate(translator, eth_type)


def translate_eth_address_type(addr_type: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "",
        "vpx": ".generatedaddress",
        "generated": ".generatedaddress",
        "static": ".address"
    })
    return translate(translator, addr_type)


def find_eths(d: defaultdict, interface: str, networks: dict) -> list:
    eths: list = []
    for x in range(10):
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        eth: defaultdict = defaultdict(str)
        s: str = f"{interface}{x}"
        eth["x"] = str(x) # XXX unused index XXX
        eth_type: str = parse_eth_type(d[s + ".connectiontype"])
        eth_name: str = d[s + ".networkname"]
        onet: str = ""

        if (eth_name and len(networks["name"]) > 0):
            if (eth_name in networks["name"]):
                onet = networks["name"][eth_name]
            elif (networks["name"]["*"]):
                onet = networks["name"]["*"]
        if (onet == "" and len(networks["type"]) > 0):
            if (eth_type in networks["type"]):
                onet = networks["type"][eth_type]
            elif (networks["type"]["*"]):
                onet = networks["type"]["*"]
        if (onet == "" and eth_type):
            onet = translate_eth_type(eth_type)
        if (onet == ""):
            log.warning("%s: no meaningful network mapping found, defaulting to bridge.", s)
            onet = "bridge"

        eth["type"] = onet
        eth["model"] = translate_eth_model(d[s + ".virtualdev"])
        addr_type: str = translate_eth_address_type(d[s + ".addresstype"])
        if (addr_type):
            eth["mac"] = d[s + addr_type]
        else:
            eth["mac"] = d[s + ".address"]
            if not (eth["mac"]):
                eth["mac"] = d[s + ".generatedaddress"]
        eth["addr_type"] = addr_type
        eths.append(eth)
    return eths

### emulation targets for disks and networks

# def translate_disk_target(s: str) -> str:
#     translator: defaultdict = defaultdict(str, {
#         "":           "",
#         "scsi":       "virtio",
#         "sata":       "virtio",
#         "ide":        "ide",
#         "nvme":       "virtio"
#     })
#     return translate(translator, s);


def convert_path(sourcepath: str, targetpath: str, disk_mode: str, raw: bool, datastores: dict, conv_mode: str,
                 adj_mode: str, adj_actions: dict, osd: dict,
                 trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int) -> str:
    os.makedirs(os.path.dirname(targetpath), exist_ok=True)
    if (disk_mode != "convert"):
        # we need to create a pseudo disk for the virt install command to succeed
        if (not os.path.exists(targetpath)):
            open(targetpath, 'a').close()
        return targetpath

    # CONVERSION / MOVE asked
    assert(disk_mode == "convert")
    if (sourcepath.endswith(".vmdk")):
        has_os: bool
        if (osd["name"]):
            has_os = True
        else:
            # we cannot adjust a disk that has no OS, and we cannot directly call v2v on it.
            # We use "y" as the closest replacement for "v2v" disk mode in this case.
            has_os = False
            adj_mode = "none"
            if (conv_mode == "v2v"):
                conv_mode = "y"

        if (conv_mode == "v2v"):
            img_v2v_convert(sourcepath, targetpath, trace_cmd, numa_node, raw)
        elif (conv_mode == "x"):
            img_qemu_convert(sourcepath, targetpath, adj_mode, adj_actions, trace_cmd, cache_mode, numa_node, parallel, raw)
        elif (conv_mode == "y"):
            img_qemu_nbd_convert(sourcepath, targetpath, adj_mode, adj_actions, trace_cmd, cache_mode, numa_node, parallel, raw)
        else:
            assert(0) # unhandled conv_mode value

    elif (targetpath != sourcepath):
        try:
            if (filecmp.cmp(sourcepath, targetpath, shallow=True)):
                log.info("disk already found at %s, no need to copy.", targetpath)
                return targetpath
        except:
            log.info("could not compare to %s, assume we need to copy.", targetpath)

        log.info("copying non-VMDK disk %s", targetpath)
        # use copy2 so we try to preserve modification time.
        shutil.copy2(sourcepath, targetpath)

    return targetpath


def virt_install(vinst_version: float,
                 vmx_name: str, xml_name: str, fidelity: bool,
                 disk_mode: str, raw: bool, skip_extra: bool, datastores: dict, conv_mode: str,
                 adj_mode: str, adj_actions: dict,
                 trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int,
                 displayname: str, annotation: str,
                 cpu: dict, memory: int,
                 vcpus: int, sockets: int, cores: int, threads: int, vm_affinity: str,
                 iothreads: int,
                 genid: str, sysinfo: str,
                 uefi: str, nvram: list,
                 svga: bool, svga_memory: int, vga: bool,
                 sound: str,
                 disk_ctrls: dict, disks: list, floppys: list,
                 eths: list) -> None:
    ### GENERAL SECTION - General Options for selecting the main functionality ###
    args: list = [ "virt-install", "--print-xml", "--dry-run", "--noautoconsole", "--check", "all=off" ]
    args.extend(["--virt-type", "kvm"])

    # for Windows 2012, "PC" is necessary to boot, with legacy BIOS.
    args.extend(["--machine", "q35" if (uefi) else "pc"])

    # Starting with virt-install 4.0.0 providing osinfo is REQUIRED which breaks scripts,
    # and especially unfriendly with our import use case.
    # To avoid this there is an environment variable to set, VIRTINSTALL_OSINFO_DISABLE_REQUIRE=1
    # but it emits a warning. Disable the check explicitly via cmdline option instead.
    # sub_env = os.environ.copy()
    # sub_env["VIRTINSTALL_OSINFO_DISABLE_REQUIRE"] = "1"
    if (vinst_version >= 4.0):
        args.extend(["--os-variant", "detect=on,require=off"])

    ### MAIN VM INFO SECTION - Fundamental VM Options are set here ###
    (domainname, n) = re.subn(r"\.xml$", "", os.path.basename(xml_name), count=1, flags=re.IGNORECASE)
    if (n != 1):
        log.critical("invalid xml name %s, does not end in .xml", xml_name)
        sys.exit(1)
    args.extend(["--name", domainname])
    if (displayname):
        args.extend(["--metadata", f"title={displayname}"])
    if (annotation):
        args.extend(["--metadata", f"description={annotation}"])
    assert(memory > 0)
    args.extend(["--memory", f"{memory}"])
    assert(cpu["model"])

    cpu_str: str = cpu["model"]
    if (vinst_version >= 4.0 and cpu["model"] == "host-passthrough"):
        cpu_str += ",check=none,migratable=on"
    args.extend(["--cpu", cpu_str])

    assert(vcpus > 0 and sockets > 0 and cores > 0 and threads > 0)
    vcpu_str = f"{vcpus},sockets={sockets},cores={cores},threads={threads}"
    if (fidelity and vm_affinity):
        vcpu_str += f",cpuset={vm_affinity}"
    args.extend(["--vcpus", vcpu_str])
    assert(iothreads > 0)
    args.extend(["--iothreads", f"{iothreads}"])

    ### FIRMWARE and BOOT SECTION - BIOS, UEFI, etc ###
    if (uefi):
        args.extend(["--boot", f"{uefi}"])

    ### XXX not safe, removed to avoid destroying nvram XXX ###
    ### we'd need to convert from the VMWare nvram format ###
    #
    #if (nvram):
    #    args.extend(["--boot", f"nvram={nvram}"])

    if (genid):
        args.extend(["--metadata", f"genid={genid}"])
    if (sysinfo):
        args.extend(["--sysinfo", sysinfo])

    ### MULTIMEDIA SECTION - display, graphics, sound ###
    args.extend(["--graphics", "vnc"])

    args.append("--video")

    if (vga):
        args.append("model.type=vga")
    elif (svga):
        args.append("model.type=cirrus")
    else:
        args.append("model.type=none")

    if (sound):
        args.extend(["--sound", f"model={sound}"])

    ### EVENTS SECTION ###
    args.extend(["--events", "on_crash=restart"])

    ### DISKS AND CONTROLLERS SECTION ###
    s: str; model: str; device: str; driver: str; path: str
    if (fidelity):
        # only in fidelity mode we explicitly add controllers as present in the original config file,
        # only translating VMWare PV to Virtio PV (pvscsi to virtio-scsi).
        # Otherwise we let libvirt add controllers and use virtio-blk for everything we can.
        for interface in disk_ctrls:
            # only 1 IDE controller is supported by virt-install/libvirt,
            # we will have this automatically inserted if targeted by a disk
            # so we omit it here.
            if (interface == "ide"):
                continue
            ### XXX libvirt does not support nvme, so we add them as virtio disks ###
            if (interface == "nvme"):
                continue
            ctrls: dict = disk_ctrls[interface]
            for index in ctrls:
                ctrl = ctrls[index]
                s = f"type={interface},index={index}"
                model = ctrl["model"]
                if (model):
                    s += f",model={model}"
                args.extend(["--controller", s])

    for disk in disks:
        x: int = disk["x"]
        y: int = disk["y"]
        device = disk["device"]
        paths: tuple = disk["path"]
        if (skip_extra and not (disk["os"]["name"])):
            log.info("skipping extra non-OS disk %s", paths[0])
            continue
        stopwatch_start()
        path = convert_path(paths[0], paths[1], disk_mode, raw, datastores, conv_mode,
                            adj_mode, adj_actions, disk["os"],
                            trace_cmd, cache_mode, numa_node, parallel)
        if (disk_mode == "convert"):
            elapsed: float = stopwatch_elapsed()
            if (elapsed > 0.0):
                targetstat = os.stat(path)
                targetsize = targetstat.st_blocks * 512 // (1024 * 1024)
                log.info("%s MiB in %s sec = %s MiB/s",
                         targetsize, elapsed, targetsize // elapsed)

        bus: str = disk["bus"]
        cache: str = disk["cache"]
        driver = disk["driver"]
        target: str
        if (fidelity or device == "cdrom"):
            target = "virtio" if (bus == "nvme") else bus
        else:
            target = "virtio"
        s = f"device={device},path={path},target.bus={target},driver.cache={cache}"
        if (vinst_version >= 3.0):
            s += f",type={driver}"
        args.extend(["--disk", s])

    for paths in floppys:
        if not all (paths):
            continue
        device = "floppy"
        path = paths[1]
        driver = "file"

        s = f"device={device},path={path}"
        if (vinst_version >= 3.0):
            s += f",type={driver}"
        args.extend(["--disk", s])

    if not disks and not floppys[0] and not floppys[1]:
        args.extend(["--disk", "none"])

    ### NETWORKS ###

    for eth in eths:
        s = eth["type"]
        model = eth["model"]
        mac: str = eth["mac"]
        if (model):
            s += f",model={model}"
        if (mac and eth["addr_type"] == ".address"):
            s += f",mac={mac}"
        args.extend(["--network", s])

    ### COMMUNICATIONS, GUEST-AGENT ###
    args.extend(["--vsock", "cid.auto=yes"])
    args.extend(["--controller", "type=virtio-serial,model=virtio"])
    args.extend(["--channel", "unix,mode=bind,target_type=virtio,name=org.qemu.guest_agent.0"])
    # allow copypaste to work (XXX does not really work for me XXX)
    args.extend(["--channel", "qemu-vdagent,source.clipboard.copypaste=on,target.type=virtio"])

    ### MISCELLANEOUS DEVICES ###
    args.extend(["--rng", "/dev/urandom"])
    args.extend(["--memballoon", "none"])

    log.debug("%s", args)

    ### WRITE THE RESULTING DOMAIN XML ###
    xml_file = open(xml_name, 'w', encoding="utf-8") if (xml_name) else sys.stdout
    try:
        subprocess.run(args, stdout=xml_file, check=True, encoding='utf-8')
    except:
        log.critical(" ".join(args))
        sys.exit(1)

    if (xml_name):
        xml_file.close()


# detect virt-install version only considering major.minor
def detect_vinst_version() -> float:
    v: float = runcmd_detectv([ "virt-install", "--version" ], r"^(\d+\.\d+)", True)
    if (v < 2.2):
        log.critical("virt-install version >= 2.2.0 is required for this command to work")
        sys.exit(1)
    if (v < 4.0):
        log.warning("virt-install version >= 4.0.0 is recommended for best results")
    return v


def detect_qemu_img_version() -> float:
    return runcmd_detectv([ "qemu-img", "--version" ], r"^.*version (\d+\.\d+)", True)


def is_dir(string: str) -> bool:
    try:
        if (os.path.isdir(string)):
            return True
    except:
        log.warning("could not stat %s", string)

    return False


def help_datastores() -> None:
    print("HELP DATASTORES (-d, --datastore RIDS,IDS=ODS)\n\n"
          "By default the directory containing the input VMX and its parent are added to the input datastores,\n"
          "and the directory containing the output XML and its parent are added to the output datastores.\n"
          "This covers the simplest case, where a VM VMDK disks are all contained in the same directory as the .vmx file,\n"
          "and referenced ISO installation images are present in the parent directory.\n\n"
          "For VMs with more disks, potentially spread across datastores, we need to know how to map them to a target datastore.\n"
          "Also, the input .vmx file will contain references to the disks that use a VMWare vmfs path that might be different\n"
          "from the local path under which those input disks are reachable on this libvirt conversion host.\n\n"
          "Option -d adds such a mapping, from .vmx Reference to an Input Datastore prefix (RIDS),\n"
          "to a locally reachable Input Datastore prefix (IDS),\n"
          "to a locally reachable and final Output Datastore prefix (ODS).\n"
          "To add further datastore mapping provide multiple -d options.\n\n"
          "EXAMPLE\n\n"
          "-d /vmfs/volumes/datastore2/,/share/datastore2/=/share/libvirt-datastore2/\n\n"
          "...\n\n"
          "All references in the VMX file to disk paths starting with '/vmfs/volumes/datastore2/' will be replaced with\n"
          "'/share/datastore2/' to be able to find the input disk files on this host.\n\n"
          "In the output XML file, the disks matching this pattern will be translated, copied, converted to /share/libvirt-datastore2/\n\n"
          "SHORT FORMS\n\n"
          "The ',' input reference translation can be omitted if VMWare and this host see /vmfs/volumes/datastore2/ as the same path:\n"
          "-d /vmfs/volumes/datastore2/=/vmfs/volumes/libvirt-isos/\n\n"
          "The '=' output translation can also be omitted when input datastore is the same as the output:\n"
          "-d /vmfs/volumes/isos,/share/isos/\n\n"
          "There is no translation of the output path to output xml reference, so ensure the output datastore path is final.\n\n"
          "The simplest scenario is where all input volumes can be reached via /vmfs/volumes/,\n"
          "and need to be translated and converted to the same path prefix:\n"
          "-d /vmfs/volumes/=/share/libvirt-volumes/\n\n")
    sys.exit(0)


def help_networks() -> None:
    print('HELP NETWORKS (-n, --network [type:|name:]INET=ONET)\n\n'
          'CONNECTION NAME MAPPINGS\n'
          '========================\n\n'
          'There are no default name mappings.\n'
          'You can specify them using the name: prefix and specifying a virt-install network type after the first = sign.\n'
          'The use of the name: prefix is optional. For example:\n'
          '-n name:ABCDEF01QP1208=network=mynat\n'
          '-n myvmnetwork=bridge=br1\n'
          '...\n\n'
          'CONNECTION TYPE MAPPINGS\n'
          '========================\n\n'
          'If there is no specific name mapping for an input network, the program will attempt to translate the network\n'
          'using its type, by supplying the type: prefix and specifying a virt-install network type after the first = sign.\n'
          'The use of the type: prefix is MANDATORY. For example:\n'
          '-n type:bridged=bridge=br0\n'
          '-n type:hostonly=bridge=br1\n'
          '-n type:nat=network=mynat\n'
          '...\n\n'
          'FALLBACK MAPPINGS\n'
          '========================\n\n'
          'If no mappings are available for type input network, the program performs a type-based automatic mapping as follows:\n'
          ' - "bridged" or "vmnet0" => "bridge" interface, with the first bridge name detected on the host, as per virt-install.\n'
          ' - "hostonly" or "vmnet1" => "network=isolated", which needs to be already defined on the host\n'
          ' - "nat" or "vmnet8" => "network=default", which needs to be already defined on the host\n\n'
          )
    sys.exit(0)


def help_conversion() -> None:
    print("HELP CONVERSION\n\n"
          "By default virt-v2v is used to convert the VMDK to .qcow2 or .raw,\n"
          "which also includes many adjustments to the guestfs for running on KVM.\n"
          "In this mode of operation, no advanced options will be available,\n"
          "as virt-v2v does not offer any control over the parameters it uses internally.\n\n"
          "VMDK EXPERIMENTAL AND ADVANCED OPTIONS\n\n"
          "For more control over the conversion operation, you can choose:\n"
          "-x which uses qemu-img convert,\n"
          "-y which uses qemu-nbd and nbdcopy.\n\n"
          "When either -x or -y are selected, all the VMDK ADVANCED OPTIONS can be used\n"
          "to fine tune the conversion procedure.\n\n"
          "GUESTFS ADJUSTMENT\n\n"
          "The changes to the VM guest filesystem to run on KVM are done by default using\n"
          "virt-v2v and the virt-v2v-in-place commands.\n"
          "For experimental modes, one can choose the following alternative methods:\n"
          "-a which instructs the program to not perform any adjustments at all. This is used for tests.\n"
          "-A which uses adjust_guestfs.py to do a minimal adjustment,\n"
          "   just rebuilding the initrd with virtio drivers and trimming the filesystems.\n\n")
    sys.exit(0)


def get_options(argc: int, argv: list) -> tuple:
    global log
    cache_modes: list = [ "none", "writeback", "unsafe", "directsync", "writethrough" ]
    disk_modes: list = [ "none", "translate", "convert" ]
    conv_modes: list = [ "v2v", "x", "y" ]
    conv_mode: str = "v2v"
    adj_modes: list = [ "none", "v2v", "x" ]
    adj_mode: str = "v2v"
    adj_actions: dict = { "drivers": True, "trim": True }

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='vmx2xml.py',
        description="converts a VMX Virtual Machine definition into a libvirt XML domain file\n"
        "and optionally translates and converts datastores.\n",
        usage="%(prog)s [options]\n"
    )
    parser.add_argument('--help-datastores', action='store_true', help='display additional help text about datastore mappings')
    parser.add_argument('--help-networks', action='store_true', help='display additional help text about network mappings')
    parser.add_argument('--help-conversion', action='store_true', help='display additional help text about disk conversions')

    inout = parser.add_argument_group('INPUT OUTPUT OPTIONS', 'main input and output for the program (REQUIRED)')
    inout.add_argument('-o', '--output-xml', action='store',
                       help='output libvirt XML file. Its directory is added to output datastores')
    inout.add_argument('-i', '--input-vmx', '-f', '--filename', metavar="VMXFILE", action='store',
                       help='the VMX description file to be converted. Its directory is added to input datastores')

    general = parser.add_argument_group('GENERAL OPTIONS', 'verbosity control, version display')
    general.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    general.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    general.add_argument('-V', '--version', action='version', version=program_version)
    general.add_argument('-O', '--overwrite', action='store_true', help='run even when the output xml already exists (overwrite)')

    vmxt = parser.add_argument_group('VMX TRANSLATION OPTIONS', 'adjust how we translate VMWare .vmx to libvirt .xml')
    vmxt.add_argument('-F', '--fidelity', action='store_true',
                      help='generate an XML closer to the original VMX. Applies sched.cpu.affinity and explicitly adds a controller and disk hierarchy matching the VMX')
    vmxt.add_argument('-d', '--datastore', metavar="RIDS,IDS=ODS", action='append',
                      help='replace references starting with RIDS to IDS for finding the input disks,\n'
                      'and translate those input disk prefixes to output datastore prefix ODS.\n'
                      'Can be specified multiple times. Also see --help-datastores')
    vmxt.add_argument('-n', '--network', metavar="[type:|name:]INET=ONET", action='append',
                      help='replace references INET into network ONET. Can be specified multiple times. Also see --help-networks')
    diskmode = parser.add_argument_group('VMDK DISK MODE OPTIONS', 'how to treat references to VMDK disks in the vmx file')
    diskmode.add_argument('-t', '--translate-disks', action='store_true', help='just translate references from .vmdk to .qcow2 or .raw')
    diskmode.add_argument('-c', '--convert-disks', action='store_true', help='translate but also convert disk contents across datastores')
    diskmode.add_argument('-r', '--raw', action='store_true', help='generate .raw references and disks instead of the default .qcow2')
    diskmode.add_argument('-X', '--skip-extra', action='store_true', help='skip extra non-OS VMDK/qcow2 disks. Useful to test boot only.')

    convmode = parser.add_argument_group('VMDK DISK CONVERSION OPTIONS', 'how to convert the VMDK disks, see also --help-conversion')
    convmode.add_argument('-x', '--experimental', action='store_true', help='use qemu-img to convert the disks')
    convmode.add_argument('-y', '--experimental2', action='store_true', help='use qemu-nbd and nbdcopy to convert the disks')

    advanced = parser.add_argument_group('VMDK ADVANCED OPTIONS', 'for -x, -y modes only')
    advanced.add_argument('-p', '--parallel', action='store', type=int, default=-1, help='specify nr of threads/connections/coroutines')
    advanced.add_argument('-C', '--cache-mode', action='store', default="none", choices=cache_modes, help='img cache mode during conversions')
    advanced.add_argument('-N', '--numa-node', action='store', type=int, default=-1, help='restrict execution (mem, cpu) to NUMA node')
    advanced.add_argument('-T', '--trace-cmd', action='store_true', help='generate /tmp/trace-xxx.dat-... profile for image conversions')
    advanced.add_argument('-A', '--x-adjust', action='store_true', help='experimental minimal guest adjustments.')
    advanced.add_argument('-a', '--skip-adjust', action='store_true', help='skip adjustments to the guestfs. For testing purposes.')
    advanced.add_argument('-D', '--skip-adjust-drivers', action='store_true', help='skip adj of drivers in the guestfs specifically.')
    advanced.add_argument('-M', '--skip-adjust-trim', action='store_true', help='skip trimming of the filesystems specifically.')

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2

    # initialize logging module
    log_init(args.verbose, args.quiet)

    if (args.help_datastores):
        help_datastores()
    if (args.help_conversion):
        help_conversion()
    if (args.help_networks):
        help_networks()
    if ((not args.input_vmx) or (not args.output_xml)):
        log.critical("must specify (REQUIRED) arguments -o/--output-xml AND -i,--input-vmx")
        sys.exit(1)
    if (args.experimental and args.experimental2):
        log.critical("cannot specify both -x and -y at the same time.")
        sys.exit(1)
    if (args.skip_adjust and args.x_adjust):
        log.critical("cannot specify both -a and -A at the same time.")
        sys.exit(1)
    if (args.skip_adjust_drivers and args.skip_adjust_trim):
        log.warning("-D and -M specified together, disabling adjustments completely.")
        args.skip_adjust = True

    if (args.experimental):
        conv_mode = "x"
    elif (args.experimental2):
        conv_mode = "y"
    if (args.skip_adjust):
        adj_mode = "none"
        adj_actions["drivers"] = False
        adj_actions["trim"] = False
    elif (args.x_adjust):
        adj_mode = "x"
    if (args.skip_adjust_drivers):
        adj_actions["drivers"] = False
    if (args.skip_adjust_trim):
        adj_actions["trim"] = False

    vmx_name: str = args.input_vmx
    xml_name: str = args.output_xml
    fidelity: bool = args.fidelity
    trace_cmd: bool = args.trace_cmd
    cache_mode: str = args.cache_mode
    vmxdir: str = os.path.dirname(os.path.abspath(vmx_name))
    xmldir: str = os.path.dirname(os.path.abspath(xml_name))
    os.makedirs(xmldir, exist_ok=True)
    numa_node: int = args.numa_node
    parallel: int = args.parallel

    datastores: defaultdict = defaultdict(str, {
        ".": (vmxdir, xmldir),
        "..": (os.path.dirname(vmxdir), os.path.dirname(xmldir))
    })

    if (args.datastore):
        for i in range(0, len(args.datastore)):
            (fro, match_eq, targetpath) = args.datastore[i].partition("=")
            (ref, match_cm, sourcepath) = fro.partition(",")
            if (not match_cm):
                sourcepath = ref
            if (not match_eq):
                targetpath = sourcepath
            datastores[ref] = (sourcepath, targetpath)

    networks: defaultdict = defaultdict(str, { "name" : {}, "type" : {} })
    if (args.network):
        for i in range(0, len(args.network)):
            (inet, match_eq, onet) = args.network[i].partition("=")
            (prefix, match_cl, netinet) = inet.partition(":")
            if (not match_cl):
                prefix = "name"
                netinet = inet
            if (prefix != "name" and prefix != "type"):
                log.critical(f'invalid network map prefix "{prefix}"')
                sys.exit(1)
            networks[prefix][netinet] = onet

    disk_mode: str = "convert" if (args.convert_disks) else "translate" if (args.translate_disks) else "none"
    overwrite: bool = args.overwrite

    log.debug("[OPTIONS] vmx_name=%s xml_name=%s overwrite:%s fidelity:%s "
              "disk_mode:%s raw:%s skip_extra:%s datastores:%s networks:%s conv_mode:%s "
              "adj_mode:%s adj_actions:%s trace_cmd:%s cache_mode:%s numa_node:%s parallel:%s",
              vmx_name, xml_name, overwrite, fidelity,
              disk_mode, args.raw, args.skip_extra, datastores, networks, conv_mode,
              adj_mode, adj_actions, trace_cmd, cache_mode, numa_node, parallel)

    if (os.path.exists(xml_name)):
        if (not overwrite):
            log.warning("%s exists, skipping", xml_name)
            sys.exit(0)
        log.warning("%s exists, overwriting", xml_name)

    if not (xml_name.endswith(".xml")):
        log.critical("invalid xml name %s, does not end in .xml", xml_name)
        sys.exit(1)

    return (vmx_name, xml_name, fidelity,
            disk_mode, args.raw, args.skip_extra, datastores, networks, conv_mode,
            adj_mode, adj_actions, trace_cmd, cache_mode, numa_node, parallel)


def main(argc: int, argv: list) -> int:
    (vmx_name, xml_name, fidelity,
     disk_mode, raw, skip_extra, datastores, networks, conv_mode,
     adj_mode, adj_actions, trace_cmd, cache_mode, numa_node, parallel) = get_options(argc, argv)

    vinst_version: float = detect_vinst_version()
    vinsp_version: float = inspector_detect_version()
    adjust_version: float = adjust_guestfs_detect_version()
    qemu_img_version: float = detect_qemu_img_version()
    trace_cmd_version: float = trace_cmd_detect_version()
    if (trace_cmd and trace_cmd_version < 2.7):
        log.critical("trace-cmd functionality requested, but trace-cmd >= 2.7 NOT FOUND")
        sys.exit(1)

    vmx_file = open(vmx_name, 'r', encoding="utf-8")
    d : defaultdict = defaultdict(str)
    parse_vmx(vmx_file, d)
    vmx_file.close()

    displayname: str = d["displayname"]
    if (displayname):
        log.debug("[DISPLAYNAME] %s", displayname)
    annotation: str = d["annotation"]
    if (annotation):
        log.debug("[ANNOTATION] %s", annotation)
    memory: int = int(d["memsize"] or 1024)
    if (memory):
        log.debug("[MEMORY] %s", memory)

    genid: str = parse_genid(int(d["vm.genid"] or 0), int(d["vm.genidx"] or 0))
    if (genid):
        log.debug("[GENID] %s", genid)

    # SMBIOS.reflectHost = "TRUE"
    # SMBIOS.noOEMStrings = "TRUE"
    # smbios.addHostVendor = "TRUE"
    sysinfo: str = "host" if (parse_boolean(d["smbios.reflectHost"])) else ""
    if (sysinfo):
        log.debug("[SYSINFO] %s", sysinfo)

    vcpus: int = int(d["numvcpus"] or 0)
    if (vcpus < 1):
        vcpus = 1
    corespersocket: int = int(d["cpuid.corespersocket"] or 0)
    if (corespersocket < 1):
        corespersocket = 1

    sockets: int = vcpus // corespersocket
    cores: int = corespersocket
    threads: int = 1
    if (sockets < 1):
        sockets = 1
    assert(vcpus == sockets * cores)
    log.debug("[VCPUS] %d,sockets=%d,cores=%d,threads=%d", vcpus, sockets, cores, threads)

    # Jim suggests using host-passthrough migratable=on rather than host-model
    cpu_model: str = "host-passthrough"
    cpu_check: str = "none"
    cpu_migratable: str = "on"
    cpu: dict = { "model": cpu_model, "check": cpu_check, "migratable": cpu_migratable }
    iothreads: int = vcpus # XXX forgot the rule of thumb to set this
    vm_affinity: str = parse_vm_affinity(d["sched.cpu.affinity"])

    uefi: str = ""
    if (d["firmware"] == "efi"):
        uefi = "uefi"
        if (vinst_version >= 4.0):
            if (parse_boolean(d["uefi.secureboot.enabled"])):
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes"
            else:
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"

    nvram: list = parse_filename_ref(d["nvram"], datastores, (disk_mode != "none"), raw)
    if (uefi):
        log.debug("[UEFI] %s", uefi)

    # ignore for now
    # guestos: str = parse_guestos(d["guestos"])

    svga: bool = parse_boolean(d["svga.present"])
    svga_memory: int = int(d["svga.vramsize"] or 0) // 1024
    vga: bool = parse_boolean(d["svga.vgaonly"])
    if (vga):
        log.debug("[VGA]")
    elif (svga):
        log.debug("[SVGA] %d", svga_memory)

    sound: str = find_sound(d)
    if (sound):
        log.debug("[SOUND] %s", sound)

    # these interface names are used in vmware for disks
    disk_ctrls: dict = { "scsi": {}, "sata": {}, "nvme": {}, "ide": {} }
    disks: list = []
    for interface in disk_ctrls:
        disk_ctrls[interface] = find_disk_controllers(d, interface)
        disks.extend(find_disks(d, datastores, interface, disk_ctrls[interface], disk_mode, raw))

    floppys: list = [ [None, None], [None, None] ]
    for i in range(2):
        floppys[i] = parse_filename_ref(d[f"floppy{i}.filename"], datastores, disk_mode != "none", raw)

    eths: list = find_eths(d, "ethernet", networks)

    log.debug("%s", disk_ctrls)
    log.debug("%s", disks)
    log.debug("%s", floppys)
    log.debug("%s", eths)

    # run virt-install to generate the xml
    virt_install(vinst_version,
                 vmx_name, xml_name, fidelity, disk_mode, raw, skip_extra, datastores, conv_mode,
                 adj_mode, adj_actions,
                 trace_cmd, cache_mode, numa_node, parallel,
                 displayname, annotation,
                 cpu, memory,
                 vcpus, sockets, cores, threads, vm_affinity,
                 iothreads,
                 genid, sysinfo,
                 uefi, nvram,
                 svga, svga_memory, vga,
                 sound,
                 disk_ctrls, disks, floppys,
                 eths)
    return 0


sys.exit(main(len(sys.argv), sys.argv))
