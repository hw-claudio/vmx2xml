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
    args: list = []
    os: dict = { "name": '', "osinfo": '', "date": '' }

    args.append("virt-inspector")
    args.extend(["--no-icon", "--no-applications", "--echo-keys"])
    args.append(path)

    log.debug("%s", args)

    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        log.error("%s could not be inspected.", path)
        return os

    name_m = re.search(r"^\s*<name>(.+)</name>\s*$", s, flags=re.MULTILINE)
    osinfo_m = re.search(r"\s*<osinfo>(.+)</osinfo>\s*$", s, flags=re.MULTILINE)
    if (name_m):
        os["name"] = name_m.group(1)
    if (osinfo_m):
        os["osinfo"] = osinfo_m.group(1)

    if (os["osinfo"] and os["name"]):
        args = []
        short_id: str = os["osinfo"]
        args.extend(["osinfo-query", "os"])
        args.extend(["-f", "short-id,release-date"])
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
        (s, _) = p.communicate()
        #  win7                 | 2009-10-22
        date_m = re.search(fr"^\s*{short_id}\s*|\s*(\d+-\d+-\d+)\s*$", s, flags=re.MULTILINE)
        if (date_m):
            os["date"] = date_m.group(1)

    log.debug("[OS] %s %s %s", os["name"], os["osinfo"], os["date"])
    return os


def v2v_img_convert(vmdk: str, qcow: str) -> None:
    args: list = []
    dirname: str = os.path.dirname(qcow)
    args.extend(["virt-v2v", "--root=first"])
    args.extend(["-i", "disk"])
    args.extend(["-o", "disk"])
    args.extend(["-of", "qcow2"])
    args.extend(["-os", dirname])
    if (log.level > logging.WARNING):
        args.append("--quiet")
    if (log.level < logging.WARNING):
        args.append("--verbose")
    if (log.level <= logging.DEBUG):
        args.append("-x")
    args.append(vmdk)
    log.debug("%s", args)

    srcnames: list = glob.glob(qcow[0:-len(".qcow2")] + "-sd*")
    if (srcnames):
        first: str = srcnames[0]
        log.critical("Existing file or directory %s could be overwritten by this operation.\n"
                     "Consider removing or moving %s into another directory", first, first)
        sys.exit(1)

    p = subprocess.run(args, stdout=sys.stderr, check=True)

    # Now rename to the name we want
    srcnames: list = glob.glob(qcow[0:-len(".qcow2")] + "-sd*")
    if (len(srcnames) != 1):
        log.critical("could not find the generated disk")
        sys.exit(1)
    os.rename(srcnames[0], qcow)


# there is no annotation for Tempfile, so return type is unknown
def qemu_img_create_overlay(vmdk: str):
    tmp = tempfile.NamedTemporaryFile()
    args: list = ["qemu-img", "create", "-b", vmdk, '-F', 'vmdk', '-f', 'qcow2']
    if (log.level > logging.DEBUG):
        args.append("-q")
    args.append(tmp.name)
    log.debug("%s", args)
    p = subprocess.run(args, stdout=sys.stderr, check=True)
    return tmp


# this step is done separately, and not with virt-v2v, in order to avoid the
# additional overlay image for performance reasons, and to allow more flexibility
# in terms of control over the qemu-img parameters in the future (-m etc).
def qemu_img_convert(vmdk: str, qcow: str) -> None:
    args: list = []
    args.extend(["qemu-img", "convert"])
    args.extend(["-O", "qcow2"])
    if (log.getEffectiveLevel() <= logging.WARNING):
        args.append("-p")
    args.extend([vmdk, qcow])

    log.debug("%s", args)
    p = subprocess.run(args, stdout=sys.stderr, check=True)


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


def parse_filename(s: str, search_paths: list) -> str:
    if (s == ""):
        return s
    if (s.startswith("/dev/")):
        log.info("[DISK] %s", s)
        if not (os.path.exists(s)):
            log.warning("VM references a block device which does not exist on this host,\n"
                        "at runtime the VM will require a host with a valid device to run!")
        return s

    # find the file referenced by the vmx in the local filesystem
    basename: str = os.path.basename(s)
    log_disable_nl()
    log.info("[DISK] %s => ", basename)
    log_enable_nl()

    pathname: str = find_file_ref(basename, search_paths[0], False)
    if (pathname == ""):
        pathname = find_file_ref(basename, search_paths[1], False)
        for i in range(2, len(search_paths)):
            if (pathname != ""):
                break
            pathname = find_file_ref(basename, search_paths[i], True)
    if (pathname == ""):
        log.critical("\n%s NOT FOUND, search paths %s", basename, search_paths)
        sys.exit(1)
    log.info("%s", pathname)
    return pathname


def parse_genid(genid: int, genidx: int) -> str:
    # e9392370-2917-565e-692b-d057f46512d6
    if (genid == 0 and genidx == 0):
        return ""
    s: str = f"{genidx:016x}{genid:016x}"
    # insert the - chars in the proper position
    assert(len(s) == 32)
    result: str = s[0:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:32]
    return result


# find a file referred to by the VMX file
def find_file_ref(name: str, path: str, recurse: bool) -> str:
    pathname: str = os.path.join(path, name)
    if (os.path.exists(pathname)):
        return pathname
    if (not recurse):
        return ""
    for (root, dirs, files) in os.walk(path):
        for this in files:
            if (this == name):
                return os.path.join(root, name)
    return ""


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


def find_disks(d: defaultdict, search_paths: list, interface: str, controllers: dict) -> list:
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
                "cache": '', "path" : '',
                #"os": { "name": '', "osinfo": '', "date": '' }
            }
            t: str = d[f"{interface}{x}:{y}.devicetype"].lower()
            disk["device"] = "cdrom" if ("cdrom" in t) else "disk"
            disk["path"] = parse_filename(d[f"{interface}{x}:{y}.filename"], search_paths)
            disk["driver"] = "block" if (disk["path"].startswith("/dev/")) else "file"
            # XXX we never use the actual libvirt/qemu default, writeback?
            disk["cache"] = "writethrough" if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])) else "none"
            #if (disk["path"]):
            #    disk["os"] = virt_inspector(disk["path"])
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

def guestfs_convert(path: str) -> bool:
    args: list = []
    args.append("guestfs_adjust.py")
    v: int = 0; q: int = 0; i: int

    if (log.level < logging.WARNING):
        v = (logging.WARNING - log.level) // 10
    if (log.level > logging.WARNING):
        q = (log.level - logging.WARNING) // 10

    for i in range(v):
        args.append("-v")
    for i in range(q):
        args.append("-q")

    args.extend(["-f", path])
    log.debug("%s", args)

    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        return False
    return True


def translate_convert_path(sourcepath: str, qcow_mode: int, datastores: dict, use_v2v: bool) -> str:
    targetpath: str = sourcepath
    is_qcow: int = 0

    for datapath in datastores:
        targetpath = sourcepath.replace(datapath, datastores[datapath], 1)
        if (targetpath != sourcepath):
            break

    if (qcow_mode > 0):
        vmdk: str = targetpath
        (match, is_qcow) = re.subn(r"\.vmdk$", ".qcow2", vmdk, count=1, flags=re.IGNORECASE)
        if (is_qcow == 1):
            targetpath = match

    if (qcow_mode > 1):
        os.makedirs(os.path.dirname(targetpath), exist_ok=True)
        if (is_qcow):
            if (use_v2v):
                v2v_img_convert(sourcepath, targetpath)
            else:
                tmp = qemu_img_create_overlay(sourcepath)
                if (guestfs_convert(tmp.name)):
                    log.info("guestfs_adjust.py: successfully adjusted %s.", tmp.name)
                else:
                    log.warning("guestfs_adjust.py: no adjustment done to %s.", tmp.name)
                qemu_img_convert(tmp.name, targetpath)
                tmp.close()

        elif (targetpath != sourcepath):
            try:
                if (filecmp.cmp(sourcepath, targetpath, shallow=True)):
                    log.info("disk already found at %s, no need to copy.", targetpath)
                    return targetpath
            except:
                log.info("could not compare to %s, assume we need to copy.", targetpath)

            log.info("copy non-VMDK disk to %s", targetpath)
            # use copy2 so we try to preserve modification time.
            shutil.copy2(sourcepath, targetpath)

    return targetpath


def virt_install(vinst_version: float, qcow_mode: int, datastores: dict, use_v2v: bool,
                 xml_name: str, vmx_name: str,
                 name: str, memory: int,
                 cpu: dict,
                 vcpus: int, sockets: int, cores: int, threads: int,
                 iothreads: int,
                 genid: str, sysinfo: str,
                 uefi: str, nvram: str,
                 svga: bool, svga_memory: int, vga: bool,
                 sound: str,
                 disk_ctrls: dict, disks: list, floppys: list,
                 eths: list) -> None:
    args: list = []
    ### GENERAL SECTION - General Options for selecting the main functionality ###
    args.append("virt-install")
    args.append("--print-xml")
    args.append("--dry-run")
    args.append("--noautoconsole")
    args.extend(["--virt-type", "kvm"])
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
    args.extend(["--vcpus", f"{vcpus},sockets={sockets},cores={cores},threads={threads}"])
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

    # we fully trusted the parsing we could consider video "none", instead we default to cirrus
    args.append("--video")

    if (vga):
        args.append("model.type=vga")
    elif (svga):
        args.append("model.type=cirrus")
    else:
        args.append("model.type=none")

    if (sound):
        args.extend(["--sound", f"model={sound}"])

    ### DISKS AND CONTROLLERS SECTION ###
    ### XXX needs testing with interface "nvme", what to do about nvme0, nvme1...? ###

    for interface in disk_ctrls:
        # only IDE controller is supported by virt-install/libvirt
        # we will have this automatically inserted if targeted by a disk
        # so we omit it here.
        if (interface == "ide"):
            continue
        ctrls: dict = disk_ctrls[interface]
        for index in ctrls:
            ctrl = ctrls[index]
            s: str = f"type={interface},index={index}"
            model: str = ctrl["model"]
            if (model):
                s += f",model={model}"
            args.extend(["--controller", s])


    for disk in disks:
        x: int = disk["x"]
        y: int = disk["y"]
        device: str = disk["device"]
        sourcepath: str = disk["path"]
        targetpath: str = translate_convert_path(sourcepath, qcow_mode, datastores, use_v2v)
        bus: str = disk["bus"]
        cache: str = disk["cache"]
        driver: str = disk["driver"]
        #target: str = bus if (disk["device"] == "cdrom") else translate_disk_target(bus)
        target: str = bus
        s: str = f"device={device},path={targetpath},target.bus={target},driver.cache={cache}"
        if (vinst_version >= 3.0):
            s += f",type={driver}"
        args.extend(["--disk", s])

    for disk in floppys:
        if not disk:
            continue
        device: str = "floppy"
        path: str = disk
        driver: str = "file"

        s: str = f"device={device},path={path}"
        if (vinst_version >= 3.0):
            s += f",type={driver}"
        args.extend(["--disk", s])

    if not disks and not floppys[0] and not floppys[1]:
        args.extend(["--disk", "none"])

    ### NETWORKS ###

    for eth in eths:
        s: str = eth["type"]
        model: str = eth["model"]
        mac: str = eth["mac"]
        if (model):
            s += f",model={model}"
        if (mac and eth["addr_type"] == ".address"):
            s += f",mac={mac}"
        args.extend(["--network", s])

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
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
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
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect guestfs_adjust version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("guestfs_adjust: detected version %s", v)
    return v


def is_dir(string: str) -> str:
    if (os.path.isdir(string)):
        return string
    else:
        raise NotADirectoryError(string)


def get_options(argc: int, argv: list) -> tuple:
    global log
    use_v2v: bool = True
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='vmx2xml.py',
        description="converts a VMX Virtual Machine definition into a libvirt XML domain file\n",
        epilog="requires virt-install, qemu-img and guestfs_adjust"
    )
    parser.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-V', '--version', action='version', version=program_version)
    parser.add_argument('-o', '--output-xml', action='store', help='output libvirt XML file (default to stdout)')
    parser.add_argument('-s', '--storagedir', action="append",
                        help='extra input storage dirs to scan for VMDKs and other disks')
    parser.add_argument('-f', '--filename', metavar="VMXFILE", action='store', required=True,
                        help='the VMX description file to be converted')
    parser.add_argument('-t', '--translate-qcow2', action='store_true', help='translate path references from .vmdk to .qcow2')
    parser.add_argument('-c', '--convert-disks', action='store_true', help='convert and move disk contents across datastores (implies -t)')
    parser.add_argument('-x', '--experimental', action='store_true', default=False, help='use the more efficient but experimental conversion method')
    parser.add_argument('-d', '--translate-datastore', metavar="DS1=DS2", action='append',
                        help='(can be specified multiple times) translate all paths containing DS1 with DS2')

    args: argparse.Namespace = parser.parse_args()
    if (args.experimental):
        use_v2v = False
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
    if (xml_name):
        # handy to create already the path to the destination xml
        os.makedirs(os.path.dirname(xml_name), exist_ok=True)

    vmxdir: str = os.path.dirname(vmx_name)
    search_paths: list = [ vmxdir, os.path.join(vmxdir, ".." ) ]
    if (args.storagedir):
        search_paths.extend(args.storagedir)

    datastores: defaultdict = defaultdict(str)
    if (args.translate_datastore):
        for i in range(0, len(args.translate_datastore)):
            (fro, match, to) = args.translate_datastore[i].partition("=")
            if not (match):
                log.critical("--translate-datastore needs a = separator");
                sys.exit(1)
            datastores[fro] = to

    qcow_mode: int = 2 if (args.convert_disks) else 1 if (args.translate_qcow2) else 0

    log.debug("[OPTIONS] vmx_name=%s xml_name=%s search_paths:%s qcow_mode:%s datastores:%s usev2v:%s",
              vmx_name, xml_name, search_paths, qcow_mode, datastores, use_v2v)
    return (vmx_name, xml_name, search_paths, qcow_mode, datastores, use_v2v)


def main(argc: int, argv: list) -> int:
    (vmx_name, xml_name, search_paths, qcow_mode, datastores, use_v2v) = get_options(argc, argv)

    vinst_version: float = detect_vinst_version()
    adjust_version: float = detect_guestfs_adjust_version()

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

    uefi: str = ""
    if (d["firmware"] == "efi"):
        uefi = "uefi"
        if (vinst_version >= 4.0):
            if (parse_boolean(d["uefi.secureboot.enabled"])):
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes,firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=yes"
            else:
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"

    nvram: str = parse_filename(d["nvram"], search_paths)
    if (uefi):
        log.debug("[UEFI] %s", uefi)

    # ignore for now
    # guestos: str = parse_guestos(d["guestos"])

    svga: bool = parse_boolean(d["svga.present"])
    svga_memory: int = int(d["svgaram.vramSize"] or 0) // 1024
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
        # XXX we will ignore the controllers XXX
        disk_ctrls[interface] = find_disk_controllers(d, interface)
        disks.extend(find_disks(d, search_paths, interface, disk_ctrls[interface]))

    floppys: list = [ "", "" ]
    for i in range(2):
        floppys[i] = parse_filename(d[f"floppy{i}.filename"], search_paths)

    eths: list = find_eths(d, "ethernet")

    log.debug("%s", disk_ctrls)
    log.debug("%s", disks)
    log.debug("%s", floppys)
    log.debug("%s", eths)

    # run virt-install to generate the xml
    virt_install(vinst_version, qcow_mode, datastores, use_v2v,
                 xml_name, vmx_name,
                 name, memory,
                 cpu,
                 vcpus, sockets, cores, threads,
                 iothreads,
                 genid, sysinfo,
                 uefi, nvram,
                 svga, svga_memory, vga,
                 sound,
                 disk_ctrls, disks, floppys,
                 eths)
    return 0


sys.exit(main(len(sys.argv), sys.argv))
