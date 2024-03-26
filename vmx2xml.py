#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#

import configparser
import sys
import os
from typing import List, Tuple
from os.path import join
from collections import defaultdict

def usage() -> None:
    print("usage: vmx2xml.py FILENAME.vmx [PATH_STORAGE]\n"
          "\n"
          "Convert a VMX Virtual Machine definition into a libvirt XML domain file,\n"
          "replacing all references to .vmdk to .qcow2, converting them with qemu-img\n"
          "\n"
          "PATH_STORAGE, if provided, is an additional path to search for referenced files.\n"
          "\n"
          "Searched PATHs:\n"
          "by default this command scans for referenced files in the same directory as\n"
          "FILENAME.vmx, then tries the current directory, then tries PATH_STORAGE and\n"
          "its subdirectories recursively if provided.\n\n"
          "This command requires qemu-img to be installed as well as virt-install\n\n"
    )
    sys.exit(1)


def parse_boolean(s: str) -> bool:
    s = s.lower()
    if (s == "true"):
        return True
    else:
        return False


def parse_filename(s: str, search_paths: List[str]) -> str:
    if (s == ""):
        return s
    if (s.startswith("/dev/")):
        return s
    # find the file referenced by the vmx in the local filesystem
    basename: str = os.path.basename(s)
    print(f"disk {basename} => ", end="")

    pathname: str = find_file_ref(basename, search_paths[0], False)
    if (pathname == ""):
        pathname = find_file_ref(basename, search_paths[1], False)
        if (pathname == "" and len(search_paths) == 3):
            pathname = find_file_ref(basename, search_paths[2], True)
    if (pathname == ""):
        print(f"NOT FOUND, search paths {search_paths}")
        sys.exit(1)
    print(f"{pathname}")
    return pathname


def parse_genid(genid: int, genidx: int) -> str:
    # e9392370-2917-565e-692b-d057f46512d6
    if (genid == 0 and genidx == 0):
        return ""
    s: str = f"{genidx:16x}{genid:16x}"
    # insert the - chars in the proper position
    assert(len(s) == 33)
    result: str = s[0:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:32]
    print(f"genid={result}")
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


def find_scsi_controller_model(d: defaultdict, x: int, interface: str) -> str:
    model: str = d[f"{interface}{x}.virtualdev"]
    if (model != ""):
        translator: defaultdict = defaultdict(str, {
            "auto":       "auto",
            "lsilogic":   "lsilogic",
            "lsisas1068": "lsisas1068",
            "pvscsi":     "vmpvscsi"
        })
        model = translator[model]
        if (model != ""):
            return model
    return "buslogic"


def find_disk_controllers(d: defaultdict, interface: str) -> dict:
    controllers: defaultdict = defaultdict(str)
    for x in range(4): # max is from "How Storage Controller Technology Works" VSphere7 docs
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        model: str = ""
        if (interface == "scsi"):    # Only SCSI seems to have virtualdev
            model = find_scsi_controller_model(d, x, interface)
        controllers[x] = { "model": model }
    return controllers


def find_disks(d: defaultdict, search_paths: List[str], interface: str, controllers: dict) -> List[str]:
    disks: list = []
    for x in range(4):
        if (x not in controllers):
            continue
        for y in range(30):           # max is from SATA ("How Storage Controller Technology Works" VSphere7)
            if not (parse_boolean(d[f"{interface}{x}:{y}.present"])):
                continue
            disk: defaultdict = defaultdict(str, {
                "bus": interface, "x": x, "y": y,
                "cache": "none", "path" : ""
            })
            # XXX we never use the actual libvirt/qemu default, writeback?
            if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])):
                disk["cache"] = "writethrough"

            disk["path"] = parse_filename(d[f"{interface}{x}:{y}.filename"], search_paths)
            disks.append(disk)
    return disks


def find_sound(d: defaultdict) -> str:
    translator: defaultdict = defaultdict(str, {
        "es1371":  "es1370",
        "hdaudio": "hda",
        "sb16":    "sb16",
    })
    if not (parse_boolean(d["sound.present"])):
        return ""
    if (parse_boolean(d["sound.autodetect"])):
        return "default"
    return translator[d["sound.virtualdev"]]


def find_eths(d: defaultdict, interface: str) -> list:
    eths: list = []
    for x in range(10):
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        eth: defaultdict = defaultdict(str)
        eth["name"] = d[f"{interface}{x}.networkname"]
        eth["addrtype"] = d[f"{interface}{x}.addresstype"]
        eth["conntype"] = d[f"{interface}{x}.connectiontype"] # bridged, "nat", ""
        eth["model"] = d[f"{interface}{x}.virtualdev"]
        if (eth["addrtype"] == "generated"):
            eth["addr"] = d[f"{interface}{x}.generatedaddress"]
        if (eth["addrtype"] == "static"):
            eth["addr"] = d[f"{interface}{x}.address"]
        eths.append(eth)
    return eths


def main(argc: int, argv: List[str]) -> int:
    if (argc < 2 or argc > 3):
        usage()
    vmx_name: str = argv[1]
    search_paths: List[str] = [ os.path.dirname(vmx_name), "." ]
    if (argc > 2):
        search_paths.append(argv[2])

    vmx_file = open(vmx_name, 'r', encoding="utf-8")
    d : defaultdict = defaultdict(str)
    parse_vmx(vmx_file, d)
    vmx_file.close()

    name: str = d["displayname"]
    memory: int = d["memsize"]
    genid: str = parse_genid(d["vm.genid"], d["vm.genidx"])

    # SMBIOS.reflectHost = "TRUE"
    # SMBIOS.noOEMStrings = "TRUE"
    # smbios.addHostVendor = "TRUE"
    sysinfo: str = "host" if (parse_boolean(d["smbios.reflectHost"])) else ""

    vcpus: int = d["numvcpus"] if (d["numvcpus"] > 0) else 1
    corespersocket: int = d["cpuid.corespersocket"] if (d["cpuid.corespersocket"] > 0) else 1

    sockets: int = vcpus // corespersocket
    cores: int = corespersocket
    threads: int = 1
    if (sockets < 1):
        sockets = 1
    cpu: str = "host" # most performant while still opening the door to migration
    iothreads: int = vcpus # XXX forgot the rule of thumb to set this

    if (d["firmware"] == "efi"):
        uefi: str = "uefi"
        if (parse_boolean(d["uefi.secureBoot.enabled"])):
            uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes"
        else:
            uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"

    # ignore for now
    # guestos: str = parse_guestos(d["guestos"])

    svga: bool = parse_boolean(d["svga.present"])
    svga_memory: int = d["svgaram.vramSize"] / 1024
    vga: bool = parse_boolean(d["svga.vgaonly"])

    sound: str = find_sound(d)

    nvram: str = parse_filename(d["nvram"], search_paths)

    # ignore for now
    # hpet: str = d["hpet0"]

    disk_ctrls: dict = { "scsi": {}, "sata": {}, "nvme": {}, "ide": {} }
    disks: list = []
    for interface in disk_ctrls:
        disk_ctrls[interface] = find_disk_controllers(d, interface)
        disks += find_disks(d, search_paths, interface, disk_ctrls[interface])

    # XXX how do I assign disks to a specific controller in virt-install?

    floppy0: str = parse_filename(d["floppy0.filename"], search_paths)
    floppy1: str = parse_filename(d["floppy1.filename"], search_paths)

    # eths: list = find_eths(d, "ethernet")
    # print(eths)
    # virt_install(domainname, memory)
    return 0


sys.exit(main(len(sys.argv), sys.argv))
