#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Currently requires virt-install 2.2 and recommends 4.0
# also requires virt-inspector (libguestfs), including libguestfs-winsupport

import configparser
import sys
import os
import re
import subprocess
import argparse
from os.path import join
from collections import defaultdict

debug: bool = False
program_version: str = "0.1"

def printerr(arg) -> None:
    print(arg, file=sys.stderr)


def printerrln(arg) -> None:
    print(arg, file=sys.stderr, end="")


def virt_inspector(path: str) -> dict:
    args: list = []
    os: dict = { "name": '', "osinfo": '', "date": '' }

    args.append("virt-inspector")
    args.extend(["--no-icon", "--no-applications", "--echo-keys"])
    args.append(path)

    if (debug):
        printerr(args)

    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        if (debug):
            printerr(path + " could not be inspected.")
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

    if (debug):
        name: str = os["name"]
        osinfo: str = os["osinfo"]
        date: str = os["date"]
        printerr(f"{name} {osinfo} {date}")

    return os


def qcow_convert(vmdk: str, qcow: str) -> None:
    args: list = []
    args.extend(["qemu-img", "convert"])
    args.extend(["-f", "vmdk"])
    args.extend(["-O", "qcow2"])
    if (debug):
        args.append("-p")
    args.extend([vmdk, qcow])

    if (debug):
        printerr(args)

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
        printerr(f"[DISK] {s}")
        if not (os.path.exists(s)):
            try:
                open(s, 'w').close()
            except:
                printerr("VM references a block device which does not exist on this host\n"
                         "and requires privileges to create.\n"
                         "Consider manually creating a bogus file as a workaround.\n"
                        "At runtime the VM will require a host with a valid device to run!\n")
                exit(1)
        return s

    # find the file referenced by the vmx in the local filesystem
    basename: str = os.path.basename(s)
    if (debug):
        printerrln(f"[DISK] {basename} => ")

    pathname: str = find_file_ref(basename, search_paths[0], False)
    if (pathname == ""):
        pathname = find_file_ref(basename, search_paths[1], False)
        for i in range(2, len(search_paths)):
            if (pathname != ""):
                break
            pathname = find_file_ref(basename, search_paths[i], True)
    if (pathname == ""):
        printerr(f"\n{basename} NOT FOUND, search paths {search_paths}")
        sys.exit(1)
    if (debug):
        printerr(f"{pathname}")
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
        "pvscsi":     "auto"
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
                "os": { "name": '', "osinfo": '', "date": '' }
            }
            t: str = d[f"{interface}{x}:{y}.devicetype"].lower()
            disk["device"] = "cdrom" if ("cdrom" in t) else "disk"
            disk["path"] = parse_filename(d[f"{interface}{x}:{y}.filename"], search_paths)
            disk["driver"] = "block" if (disk["path"].startswith("/dev/")) else "file"
            # XXX we never use the actual libvirt/qemu default, writeback?
            disk["cache"] = "writethrough" if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])) else "none"
            if (disk["path"]):
                disk["os"] = virt_inspector(disk["path"])
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
        "bridged": "bridge=br0",
        "vmnet0": "bridge=br0",
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


def virt_install(vinst_version: float, qcow_mode: int,
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
    ### XXX currently likely dies with interface "nvme", what to do about nvme0, nvme1...? ###

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
        path: str = disk["path"]

        if (qcow_mode > 0):
            vmdk: str = path
            (match, n) = re.subn(r"\.vmdk$", ".qcow2", vmdk, count=1, flags=re.IGNORECASE)
            if (n == 1):
                path = match
                if (qcow_mode > 1):
                    qcow_convert(vmdk, path)

        bus: str = disk["bus"]
        cache: str = disk["cache"]
        driver: str = disk["driver"]
        #target: str = bus if (disk["device"] == "cdrom") else translate_disk_target(bus)
        target: str = bus
        s: str = f"device={device},path={path},target.bus={target},driver.cache={cache}"
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

    if (debug):
        printerr(args)

    ### WRITE THE RESULTING DOMAIN XML ###
    xml_file = open(xml_name, 'w', encoding="utf-8") if (xml_name) else sys.stdout
    try:
        subprocess.run(args, stdout=xml_file, check=True, encoding='utf-8')
    except:
        printerr(" ".join(args))
        sys.exit(1)

    if (xml_name):
        xml_file.close()


# detect virt-install version only considering major.minor
def detect_vinst_version() -> float:
    s: str = ""
    args: list = [ "virt-install", "--version" ]
    if (debug):
        printerr(args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        printerr(f"failed to detect virt-install version: {s}")
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    if (v < 2.2):
        printerr("virt-install version >= 2.2.0 is required for this command to work")
    if (v < 4.0):
        printerr("virt-install version >= 4.0.0 is recommended for best results")
    if (debug):
        printerr(f"virt-install: detected version {v}")
    return v


def is_dir(string: str) -> str:
    if (os.path.isdir(string)):
        return string
    else:
        raise NotADirectoryError(string)


def get_options(argc: int, argv: list) -> tuple:
    global debug
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='vmx2xml.py',
        description="converts a VMX Virtual Machine definition into a libvirt XML domain file,\n"
        "replacing all references to .vmdk to .qcow2\n",
        epilog="requires virt-install and virt-inspector, including libguestfs-winsupport"
    )
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-V', '--version', action='version', version=program_version)
    parser.add_argument('-o', '--output-xml', action='store', help='output libvirt XML file (default to stdout)')
    parser.add_argument('-s', '--storagedir', action="append",
                        help='extra input storage dirs to scan for VMDKs and other disks')
    parser.add_argument('-f', '--filename', metavar="VMXFILE", action='store', required=True,
                        help='the VMX description file to be converted')
    parser.add_argument('-q', '--qcow-translate', action='store_true', help='replace references to .vmdk to .qcow2')
    parser.add_argument('-Q', '--qcow-convert', action='store_true', help='also convert the .vmdk into .qcow2 (implies -q)')

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose):
        debug = True
    vmx_name: str = args.filename
    xml_name: str = args.output_xml
    search_paths: list = [ os.path.dirname(vmx_name), "." ]
    if (args.storagedir):
        search_paths.extend(args.storagedir)
    qcow_mode: int = 2 if (args.qcow_convert) else 1 if (args.qcow_translate) else 0
    if (debug):
        printerr(f"[OPTIONS] vmx_name={vmx_name} xml_name={xml_name} search_paths:{search_paths} qcow_mode:{qcow_mode}")
    return (vmx_name, xml_name, search_paths, qcow_mode)


def main(argc: int, argv: list) -> int:
    (vmx_name, xml_name, search_paths, qcow_mode) = get_options(argc, argv)

    vinst_version : float = detect_vinst_version()
    vmx_file = open(vmx_name, 'r', encoding="utf-8")
    d : defaultdict = defaultdict(str)
    parse_vmx(vmx_file, d)
    vmx_file.close()

    name: str = d["displayname"]
    if (debug and name):
        printerr(f"[NAME] {name}")

    memory: int = int(d["memsize"] or 1024)
    if (debug and memory):
        printerr(f"[MEMORY] {memory}")

    genid: str = parse_genid(int(d["vm.genid"] or 0), int(d["vm.genidx"] or 0))
    if (debug and genid):
        printerr(f"[GENID] {genid}")

    # SMBIOS.reflectHost = "TRUE"
    # SMBIOS.noOEMStrings = "TRUE"
    # smbios.addHostVendor = "TRUE"
    sysinfo: str = "host" if (parse_boolean(d["smbios.reflectHost"])) else ""
    if (debug and sysinfo):
        printerr(f"[SYSINFO] {sysinfo}")

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
    if (debug):
        printerr(f"[VCPUS] {vcpus},sockets={sockets},cores={cores},threads={threads}")

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
    if (debug and uefi):
        printerr(f"[UEFI] {uefi}")

    # ignore for now
    # guestos: str = parse_guestos(d["guestos"])

    svga: bool = parse_boolean(d["svga.present"])
    svga_memory: int = int(d["svgaram.vramSize"] or 0) // 1024
    vga: bool = parse_boolean(d["svga.vgaonly"])
    if (debug and vga):
        printerr(f"[VGA]")
    elif (debug and svga):
        printerr(f"[SVGA] {svga_memory}")

    sound: str = find_sound(d)
    if (debug and sound):
        printerr(f"[SOUND] {sound}")

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

    if (debug):
        printerr(disk_ctrls)
        printerr(disks)
        printerr(floppys)
        printerr(eths)

    # run virt-install to generate the xml
    virt_install(vinst_version, qcow_mode,
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
