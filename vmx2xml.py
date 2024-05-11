#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Currently requires virt-install 2.2 and recommends 4.0
# also requires qemu-img, guestfs_adjust.
#
# This tool is mostly used to configure the xml so that it more closely matches
# the configuration pre-conversion.
#

import sys
import os
import re
import subprocess
import argparse
import glob
from os.path import join
from collections import defaultdict
import logging
import shutil
import tempfile
import filecmp
import struct
import time

log: logging.Logger = logging.getLogger(__name__)
program_version: str = "0.1"

def log_disable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = ""


def log_enable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = "\n"


def virt_inspector(path: str) -> dict:
    args: list = [ "virt-inspector", "--no-icon", "--no-applications", "--echo-keys", path ]
    osd: dict = { "name": '', "osinfo": '' }

    log.debug("%s", args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        log.error("%s could not be inspected.", path)
        return osd

    name_m = re.search(r"^\s*<name>(.+)</name>\s*$", s, flags=re.MULTILINE)
    osinfo_m = re.search(r"\s*<osinfo>(.+)</osinfo>\s*$", s, flags=re.MULTILINE)
    if (name_m):
        osd["name"] = name_m.group(1)
    if (osinfo_m):
        osd["osinfo"] = osinfo_m.group(1)

    log.debug("[OS DATA] %s %s", osd["name"], osd["osinfo"])
    return osd


def numa_restrict_cmd(numa_node: int) -> list:
    return [ "numactl", "-m", str(numa_node), "-N", str(numa_node), "--" ]


def trace_cmd_start(pre: str, numa_node: int) -> int:
    args: list = []
    if (numa_node >= 0):
        # run trace-cmd on the other non-selected node
        args.extend(numa_restrict_cmd(0 if (numa_node > 0) else 1))
    tmp = tempfile.NamedTemporaryFile(delete=False, prefix=pre)
    args.extend([ "trace-cmd", "record", "-o", tmp.name, "-e", "sched" ])
    log.debug("%s", args)

    pid: int = os.fork()
    if (pid == 0):
        os.execvp(args[0], args)
    return pid


def file_ext(raw: bool) -> str:
    return "raw" if (raw) else "qcow2"


def v2v_img_convert(from_file: str, to_file: str, trace_cmd: bool, numa_node: int, raw: bool) -> None:
    to_file_ext: str = file_ext(raw)
    dirname: str = os.path.dirname(to_file)
    args: list = []

    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-v2v.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))
    args.extend([ "virt-v2v", "--root=first", "-i", "disk", "-o", "disk", "-of", to_file_ext, "-os", dirname ])

    if (log.level > logging.WARNING):
        args.append("--quiet")
    if (log.level <= logging.DEBUG):
        args.extend(["--verbose", "-x"])
    args.append(from_file)

    log.debug("%s", args)
    p = subprocess.run(args, stdout=subprocess.DEVNULL, encoding='utf-8')

    if (p.returncode == 0):
        log.info("virt-v2v: reports success converting disk %s", from_file)
    else:
        log.warning("virt-v2v: reports failure converting disk %s", from_file)

    if (trace_cmd):
        os.kill(tpid, 2)

    # Now rename to the name we want
    srcnames: list = glob.glob(to_file[0:-len(f".{to_file_ext}")] + "-sd*")
    if (len(srcnames) != 1):
        log.critical("could not find the generated disk %s", to_file)
        sys.exit(1)
    os.rename(srcnames[0], to_file)


# there is no annotation for Tempfile, so return type is unknown
def qemu_img_create_overlay(from_file: str):
    tmp = tempfile.NamedTemporaryFile()
    args: list = ["qemu-img", "create", "-b", from_file, '-F', 'vmdk', '-f', 'qcow2']
    if (log.level > logging.DEBUG):
        args.append("-q")
    args.append(tmp.name)
    log.debug("%s", args)
    p = subprocess.run(args, stdout=sys.stderr, check=True)
    return tmp


def qemu_img_create(to_file: str, vsize: int, raw: bool) -> None:
    to_file_ext: str = file_ext(raw)
    args: list = ["qemu-img", "create", "-f", to_file_ext]
    if (log.level > logging.DEBUG):
        args.append("-q")
    args.extend([to_file, str(vsize)])
    log.debug("%s", args)
    p = subprocess.run(args, stdout=sys.stderr, check=True)


def qemu_img_copy(from_file: str, to_file: str, trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, raw: bool) -> None:
    to_file_ext: str = file_ext(raw)
    args: list = []
    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-qemu-img.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))
    args.extend([ "qemu-img", "convert", "-O", to_file_ext, "-t", cache_mode, "-T", cache_mode ])
    if (parallel > 0):
        args.extend([ "-m", str(parallel) ])
    if (log.getEffectiveLevel() <= logging.WARNING):
        args.append("-p")
    args.extend([from_file, to_file])

    log.debug("%s", args)
    p = subprocess.run(args, check=True)
    if (trace_cmd):
        os.kill(tpid, 2)


def qemu_img_info(from_file: str) -> int:
    args: list = ["qemu-img", "info", "-U", from_file]

    log.debug("%s", args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()
    if (p.returncode != 0):
        log.critical("qemu-img info command failed!")
        sys.exit(1)
    # sorry, human output is way easier to parse than the json file.
    vsize_m = re.search(r"^virtual size:.*\((\d+) bytes\)", s, flags=re.MULTILINE)
    if (not vsize_m):
        log.critical("qemu-img info output could not be parsed!")
        sys.exit(1)
    return int(vsize_m.group(1))


def qemu_img_convert(sourcepath: str, targetpath: str, adjust: bool, trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, raw: bool) -> None:
    src: str = sourcepath
    if (adjust):
        tmp = qemu_img_create_overlay(sourcepath)
        guestfs_adjust(tmp.name, False)
        src = tmp.name

    qemu_img_copy(src, targetpath, trace_cmd, cache_mode, numa_node, parallel, raw)
    if (adjust):
        tmp.close()


def qemu_nbd_create(s: str, overlay: bool, cache_mode: str, raw: bool) -> tuple:
    tmp = tempfile.NamedTemporaryFile(delete=False)
    args: list = [ "qemu-nbd", f"--cache={cache_mode}", "-t", "--shared=0", "--discard=unmap", "--socket", tmp.name ]
    if (raw):
        args.extend(['-f', 'raw'])
    if (overlay):
        args.append("-s")
    args.append(s)
    log.debug("%s", args)
    pid: int = os.fork()
    if (pid == 0):
        os.execvp(args[0], args)
    args = [ "nbdinfo", f"nbd+unix:///?socket={tmp.name}" ]
    while True:
        log.debug("%s", args)
        p = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if (p.returncode == 0):
            break
        time.sleep(1)
    return (tmp, pid)


def qemu_nbd_copy(sin: str, sout: str, trace_cmd: bool, numa_node: int, parallel: int) -> None:
    args: list = []
    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-nbdcopy.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))

    args.extend([ "nbdcopy", f"nbd+unix:///?socket={sin}", f"nbd+unix:///?socket={sout}", '--requests=64', '--flush', '--progress' ])
    if (parallel > 0):
        args.extend([ '-C', str(parallel), '-T', str(parallel) ])
    log.debug("%s", args)

    p = subprocess.run(args, check=True)
    if (trace_cmd):
        os.kill(tpid, 2)


def qemu_nbd_convert(sourcepath: str, targetpath: str, adjust: bool, trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, raw: bool) -> None:
    (sin, pidin) = qemu_nbd_create(sourcepath, adjust, cache_mode, False)
    if (adjust):
        guestfs_adjust(sin.name, True)
    vsize: int = qemu_img_info(sourcepath)
    qemu_img_create(targetpath, vsize, raw)
    (sout, pidout) = qemu_nbd_create(targetpath, False, cache_mode, raw)
    qemu_nbd_copy(sin.name, sout.name, trace_cmd, numa_node, parallel)
    sin.close()
    sout.close()
    os.remove(sin.name)
    os.remove(sout.name)
    os.kill(pidin, 15)
    os.kill(pidout, 15)


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
    log_disable_nl()
    log.info("[DISK] %s => ", basename)
    log_enable_nl()

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
        to_file_ext: str = file_ext(raw)
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


def find_disks(d: defaultdict, datastores: dict, interface: str, controllers: dict, disk_mode: int, raw: bool) -> list:
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
            disk["path"] = parse_filename_ref(d[f"{interface}{x}:{y}.filename"], datastores, disk_mode >= 1, raw)
            #disk["driver"] = "block" if (disk["path"].startswith("/dev/")) else "file"
            disk["driver"] = "file"
            # XXX we never use the actual libvirt/qemu default, writeback?
            disk["cache"] = "writethrough" if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])) else "none"
            if (all(disk["path"]) and disk_mode >= 2 and disk["path"][0].endswith(".vmdk")):
                disk["os"] = virt_inspector(disk["path"][0])
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


def translate_eth_type(eth_type: str) -> str:
    translator: defaultdict = defaultdict(str, {
        "": "",
        "bridged": "bridge",
        "vmnet0": "bridge",
        "hostonly": "user",
        "vmnet1": "user",
        "nat": "network=default",
        "vmnet8": "network=default",
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


def find_eths(d: defaultdict, interface: str) -> list:
    eths: list = []
    for x in range(10):
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        eth: defaultdict = defaultdict(str)
        s: str = f"{interface}{x}"
        eth["x"] = str(x) # XXX unused XXX
        eth["type"] = translate_eth_type(d[s + ".connectiontype"])
        eth["model"] = translate_eth_model(d[s + ".virtualdev"])
        eth["name"] = d[s + ".networkname"] # XXX unused XXX
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

def guestfs_adjust(path: str, nbd: bool) -> bool:
    args: list = [ "guestfs_adjust.py", "-n" if (nbd) else "-f", path ]
    v: int = 0; q: int = 0; i: int

    if (log.level < logging.WARNING):
        v = (logging.WARNING - log.level) // 10
    if (log.level > logging.WARNING):
        q = (log.level - logging.WARNING) // 10
    for i in range(v):
        args.append("-v")
    for i in range(q):
        args.append("-q")

    log.debug("%s", args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        return False
    return True


def convert_path(sourcepath: str, targetpath: str, disk_mode: int, datastores: dict, use_v2v: int, osd: dict,
                 trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, skip_adjust: bool, raw: bool) -> str:
    os.makedirs(os.path.dirname(targetpath), exist_ok=True)
    if (disk_mode <= 1):
        # we need to create a pseudo disk for the virt install command to succeed
        if (not os.path.exists(targetpath)):
            open(targetpath, 'a').close()
        return targetpath

    # CONVERSION / MOVE asked
    assert(disk_mode >= 2)
    if (sourcepath.endswith(".vmdk")):
        has_os: bool = True if osd["name"] else False
        if (use_v2v == 1):
            if (has_os and not skip_adjust):
                v2v_img_convert(sourcepath, targetpath, trace_cmd, numa_node, raw)
            else:
                qemu_nbd_convert(sourcepath, targetpath, False, trace_cmd, cache_mode, numa_node, parallel, raw)
        elif (use_v2v == 0):
            qemu_img_convert(sourcepath, targetpath, has_os and not skip_adjust, trace_cmd, cache_mode, numa_node, parallel, raw)
        elif (use_v2v == -1):
            qemu_nbd_convert(sourcepath, targetpath, has_os and not skip_adjust, trace_cmd, cache_mode, numa_node, parallel, raw)
        else:
            assert(0) # unhandled use_v2v value

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


def virt_install(vinst_version: float, disk_mode: int, datastores: dict, use_v2v: int, fidelity: bool,
                 trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, skip_adjust: bool, skip_extra: bool, raw: bool,
                 xml_name: str, vmx_name: str,
                 name: str, memory: int,
                 cpu: dict,
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

    # for Windows 2012, "PC" is necessary to boot, with legacy BIOS, and "fidelity" mode MUST be used
    # otherwise the OS will detect the hardware change and refuse to start.
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
    if (name):
        args.extend(["--name", name])
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
        start_time: float = time.perf_counter()
        path = convert_path(paths[0], paths[1], disk_mode, datastores, use_v2v, disk["os"],
                            trace_cmd, cache_mode, numa_node, parallel, skip_adjust, raw)
        if (disk_mode >= 2):
            end_time: float = time.perf_counter();
            elapsed: float = end_time - start_time
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
    s: str = ""
    args: list = [ "virt-install", "--version" ]

    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("virt-install NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect virt-install version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    if (v < 2.2):
        log.critical("virt-install version >= 2.2.0 is required for this command to work")
        sys.exit(1)
    if (v < 4.0):
        log.warning("virt-install version >= 4.0.0 is recommended for best results")
    log.info("virt-install: detected version %s", v)
    return v


# detect guestfs_adjust version only considering major.minor
def detect_guestfs_adjust_version() -> float:
    s: str = ""
    args: list = [ "guestfs_adjust.py", "--version" ]

    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("guestfs_adjust.py NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect guestfs_adjust.py version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("guestfs_adjust.py: detected version %s", v)
    return v


def detect_qemu_img_version() -> float:
    args: list = [ "qemu-img", "--version" ]
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("qemu-img NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^.*version (\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect qemu-img version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("qemu-img: detected version %s", v)
    return v


def detect_trace_cmd_version() -> float:
    args: list = [ "trace-cmd", "-h" ]
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.info("trace-cmd NOT FOUND, cannot use tracing features")
        return 0
    (s, _) = p.communicate()
    m = re.search(r"^.*version (\d+\.\d+)", s, flags=re.MULTILINE)
    if not (m):
        log.info("trace-cmd version not detected, cannot use tracing features")
        return 0
    v: float = float(m.group(1)) or 0
    log.info("trace-cmd: detected version %s", v)
    return v


def is_dir(string: str) -> bool:
    try:
        if (os.path.isdir(string)):
            return True
    except:
        log.warning("could not stat %s", string)

    return False


def get_options(argc: int, argv: list) -> tuple:
    global log
    cache_modes: list = [ "none", "writeback", "unsafe", "directsync", "writethrough" ]
    use_v2v: int = 1
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='vmx2xml.py',
        description="converts a VMX Virtual Machine definition into a libvirt XML domain file\n"
        "and optionally translates and converts datastores.\n",
        usage="%(prog)s [options]\n"
        "\n"
        "INPUT DATASTORES: by default the directory containing the input VMX and its parent are used as the input datastore.\n"
        "OUTPUT DATASTORES: by default the directory containing the output XML and its parent is used as the output datastore.\n\n"
        "To add further datastores provide multiple -d options, for example:\n\n"
        "-d /vmfs/volumes/datastore2/,/share/volumes/datastore2/=/share/libvirt-datastore2/\n"
        "...\n\n"
        "All references in the VMX file to paths starting with '/vmfs/volumes/datastore2/' will be translated to\n"
        "'/share/volumes/datastore2', assuming the path is reachable on this host.\n\n"
        "The file will then be copied or converted to /share/libvirt-datastore2/\n\n"
        "The ',' input reference translation could be omitted if this host sees /vmfs/volumes/datastore2/ as the same path:\n"
        "-d /vmfs/volumes/datastore2/=/vmfs/volumes/libvirt-isos/\n"
        "The '=' output translation can also be omitted when input datastore is the same as the output:\n"
        "-d /vmfs/volumes/isos,/share/volumes/isos/\n\n"
        "There is no translation of the output path to output xml reference, so ensure the output datastore path is final.\n\n"
        "The simplest scenario is where all volumes can be reached via /vmfs/volumes/, and need to be translated to the same path:\n\n"
        "-d /vmfs/volumes/=/vmfs/libvirt-volumes/\n\n"
    )
    parser.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-V', '--version', action='version', version=program_version)
    parser.add_argument('-o', '--output-xml', action='store', help='output libvirt XML file', required=True)
    parser.add_argument('-f', '--filename', metavar="VMXFILE", action='store', required=True,
                        help='the VMX description file to be converted. Its directory is added to input datastores')
    parser.add_argument('-t', '--translate-disks', action='store_true', help='translate path references from .vmdk to .qcow2 or .raw')
    parser.add_argument('-c', '--convert-disks', action='store_true', help='convert and move disk contents across datastores (implies -t)')
    parser.add_argument('-O', '--overwrite', action='store_true', help='run even when the output xml already exists (overwrite)')
    parser.add_argument('-x', '--experimental', action='store_true', help='use experimental old conversion method (qemu-img)')
    parser.add_argument('-y', '--experimental2', action='store_true', help='use experimental new conversion method (qemu-nbd)')
    parser.add_argument('-C', '--cache-mode', action='store', default="none", help=f'{cache_modes} for qemu-nbd and qemu-img convert')
    parser.add_argument('-T', '--trace-cmd', action='store_true', help='generate /tmp/trace-xxx.dat-... profile for image conversions')
    parser.add_argument('-d', '--datastore', metavar="RIDS,IDS=ODS", action='append',
                        help='(can be specified multiple times) translate references starting with RIDS to IDS, then convert to ODS')
    parser.add_argument('-F', '--fidelity', action='store_true', help='configuration fidelity mode. Default is to privilege performance')
    parser.add_argument('-N', '--numa-node', action='store', type=int, default=-1, help='restrict execution (mem, cpu) to NUMA node')
    parser.add_argument('-p', '--parallel', action='store', type=int, default=-1, help='specify threads/connections/coroutines')
    parser.add_argument('-a', '--skip-adjust', action='store_true', help='skip guest adjustments to run on KVM')
    parser.add_argument('-X', '--skip-extra', action='store_true', help='skip extra non-OS VMDK/qcow2 disks')
    parser.add_argument('-r', '--raw', action='store_true', help='generate RAW disk images instead of QCOW2')

    args: argparse.Namespace = parser.parse_args()
    if (args.experimental and args.experimental2):
        log.critical("cannot specify both -x and -y at the same time.")
        sys.exit(1)
    if (args.experimental):
        use_v2v = 0
    elif (args.experimental2):
        use_v2v = -1
    if (args.cache_mode not in cache_modes):
        log.critical("cache_mode must be one of %s.", cache_modes)
        sys.exit(1)
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2
    loglevel: int = logging.WARNING - (args.verbose * 10) + (args.quiet * 10)

    log.setLevel(loglevel)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt='%(message)s'))
    log.addHandler(handler)

    vmx_name: str = args.filename
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

    disk_mode: int = 2 if (args.convert_disks) else 1 if (args.translate_disks) else 0
    overwrite: bool = args.overwrite

    log.debug("[OPTIONS] vmx_name=%s xml_name=%s disk_mode:%s datastores:%s usev2v:%s overwrite:%s fidelity:%s "
              "trace_cmd:%s cache_mode:%s numa_node:%s parallel:%s skip_adjust:%s skip_extra:%s raw:%s",
              vmx_name, xml_name, disk_mode, datastores, use_v2v, overwrite, fidelity,
              trace_cmd, cache_mode, numa_node, parallel, args.skip_adjust, args.skip_extra, args.raw)

    if (os.path.exists(xml_name)):
        if (not overwrite):
            log.warning("%s exists, skipping", xml_name)
            sys.exit(0)
        log.warning("%s exists, overwriting", xml_name)

    return (vmx_name, xml_name, disk_mode, datastores, use_v2v, fidelity,
            trace_cmd, cache_mode, numa_node, parallel, args.skip_adjust, args.skip_extra, args.raw)


def main(argc: int, argv: list) -> int:
    (vmx_name, xml_name, disk_mode, datastores, use_v2v, fidelity,
     trace_cmd, cache_mode, numa_node, parallel, skip_adjust, skip_extra, raw) = get_options(argc, argv)

    vinst_version: float = detect_vinst_version()
    adjust_version: float = detect_guestfs_adjust_version()
    qemu_img_version: float = detect_qemu_img_version()
    trace_cmd_version: float = detect_trace_cmd_version()
    if (trace_cmd and trace_cmd_version < 2.7):
        log.critical("trace-cmd functionality requested, but trace-cmd >= 2.7 NOT FOUND")
        sys.exit(1)

    vmx_file = open(vmx_name, 'r', encoding="utf-8")
    d : defaultdict = defaultdict(str)
    parse_vmx(vmx_file, d)
    vmx_file.close()

    name: str = d["displayname"]
    if (name):
        log.debug("[NAME] %s", name)

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

    nvram: list = parse_filename_ref(d["nvram"], datastores, (disk_mode >= 1), raw)
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
        floppys[i] = parse_filename_ref(d[f"floppy{i}.filename"], datastores, disk_mode >= 1, raw)

    eths: list = find_eths(d, "ethernet")

    log.debug("%s", disk_ctrls)
    log.debug("%s", disks)
    log.debug("%s", floppys)
    log.debug("%s", eths)

    # run virt-install to generate the xml
    virt_install(vinst_version, disk_mode, datastores, use_v2v, fidelity,
                 trace_cmd, cache_mode, numa_node, parallel, skip_adjust, skip_extra, raw,
                 xml_name, vmx_name,
                 name, memory,
                 cpu,
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
