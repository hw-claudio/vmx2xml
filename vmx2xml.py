#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Currently requires virt-install 2.2 and recommends 4.0

import configparser
import sys
import os
import re
import subprocess
from os.path import join
from collections import defaultdict

debug: bool = False

def usage() -> None:
    print("usage: vmx2xml.py FILENAME.vmx [PATH_STORAGE]\n"
          "\n"
          "Convert a VMX Virtual Machine definition into a libvirt XML domain file,\n"
          "replacing all references to .vmdk to .qcow2\n"
          "\n"
          "XXX Possibly in the future converting VMDK to QCOW2, or it could be a separate script XXX\n"
          "\n"
          "PATH_STORAGE, if provided, is an additional path to search for referenced files.\n"
          "\n"
          "Searched PATHs:\n"
          "by default this command scans for referenced files in the same directory as\n"
          "FILENAME.vmx, then tries the current directory, then tries PATH_STORAGE and\n"
          "its subdirectories recursively if provided.\n\n"
    )
    sys.exit(1)


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
        return s
    # find the file referenced by the vmx in the local filesystem
    basename: str = os.path.basename(s)
    print(f"[DISK] {basename} => ", end="")

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
    for x in range(4):               # from "How Storage Controller Technology Works" VSphere7 docs
        if not (parse_boolean(d[f"{interface}{x}.present"])):
            continue
        model: str = ""
        if (interface == "scsi"):    # Only SCSI seems to have virtualdev
            model = find_scsi_controller_model(d, x, interface)
        controllers[x] = { "x": x, "model": model }
    return controllers


def find_disks(d: defaultdict, search_paths: list, interface: str, controllers: dict) -> list:
    disks: list = []
    for x in range(4):
        if (x not in controllers):
            continue
        for y in range(30):           # max is from SATA ("How Storage Controller Technology Works" VSphere7)
            if not (parse_boolean(d[f"{interface}{x}:{y}.present"])):
                continue
            disk: defaultdict = defaultdict(str, {
                "bus": interface, "x": x, "y": y,
                "device": "disk",
                "cache": "none", "path" : ""
            })
            # XXX we never use the actual libvirt/qemu default, writeback?
            if (parse_boolean(d[f"{interface}{x}:{y}.writethrough"])):
                disk["cache"] = "writethrough"

            disk["path"] = parse_filename(d[f"{interface}{x}:{y}.filename"], search_paths)
            t: str = d[f"{interface}{x}:{y}.devicetype"].lower()
            if ("cdrom" in t):
                disk["device"] = "cdrom"
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


def virt_install(vinst_version: str, xml_name: str, vmx_name: str,
                 name: str, memory: int,
                 cpu_model: str,
                 vcpus: int, sockets: int, cores: int, threads: int,
                 iothreads: int,
                 genid: str, sysinfo: str,
                 uefi: str,
                 svga: bool, svga_memory: int, vga: bool,
                 sound: str,
                 nvram: str,
                 disk_ctrls: dict,
                 disks: list,
                 floppys: dict) -> None:
    args: list = []
    ### GENERAL SECTION - General Options for selecting the main functionality ###
    args.append("virt-install")
    args.append("--print-xml")
    args.append("--dry-run")
    args.append("--noautoconsole")
    args.extend(["--virt-type", "kvm"])

    # Starting with virt-install 4.0.0 providing osinfo is REQUIRED which breaks scripts,
    # and especially unfriendly with our import use case.
    # To avoid this there is an environment variable to set, VIRTINSTALL_OSINFO_DISABLE_REQUIRE=1
    # but it emits a warning. Disable the check explicitly via cmdline option instead.
    # sub_env = os.environ.copy()
    # sub_env["VIRTINSTALL_OSINFO_DISABLE_REQUIRE"] = "1"
    if (vinst_version >= 4.0):
        args.extend(["--osinfo", "detect=on,require=off"])

    ### DISABLED SECTION - Currently disabled, might be enabled in the future ###
    args.extend(["--controller", "type=usb,model=none"])
    # ignore HPET for now
    # hpet: str = d["hpet0"]

    ### MAIN VM INFO SECTION - Fundamental VM Options are set here ###
    if (name):
        args.extend(["--name", name])
    assert(memory > 0)
    args.extend(["--memory", f"{memory}"])
    assert(cpu_model)
    args.extend(["--cpu", cpu_model])
    assert(vcpus > 0 and sockets > 0 and cores > 0 and threads > 0)
    args.extend(["--vcpus", f"{vcpus},sockets={sockets},cores={cores},threads={threads}"])
    assert(iothreads > 0)
    args.extend(["--iothreads", f"{iothreads}"])

    ### FIRMWARE and BOOT SECTION - BIOS, UEFI, etc ###
    if (uefi):
        args.extend(["--boot", f"{uefi}"])
    if (nvram):
        args.extend(["--boot", f"nvram={nvram}"])
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
        video: str = "model.type=vmvga"
        if (svga_memory > 0):
            video += f",model.vram={svga_memory}"
        args.append(video)
    else:
        args.append("model.type=cirrus")

    if (sound):
        args.extend(["--sound", f"model={sound}"])

    ### DISKS AND CONTROLLERS SECTION ###
    ### XXX currently dies with interface "nvme", what to do about nvme0, nvme1...? ###

    for interface in disk_ctrls:
        ctrls: dict = disk_ctrls[interface]
        for index in ctrls:
            ctrl = ctrls[index]
            s: str = f"type={interface},index={index}"
            model: str = ctrl["model"]
            if (model):
                s += f",model={model}"
                if (vinst_version >= 4.0):
                    s += f",queues={vcpus}"
            args.extend(["--controller", s])

    if not disks:
        args.extend(["--disk", "none"])

    for disk in disks:
        #disk: defaultdict = defaultdict(str, {
        #    "bus": interface, "x": x, "y": y,
        #    "cache": "none", "path" : ""
        #})
        x: int = disk["x"]
        y: int = disk["y"]
        device: str = disk["device"]
        path: str = disk["path"]
        bus: str = disk["bus"]
        cache: str = disk["cache"]
        # XXX for /dev/ we might have to use the source.dev attribute instead of source.file XXX #

        s: str = f"type=file,device={device},source_file={path},target.bus={bus},driver.cache={cache}"

        # based on googling around, vmx scsix:y should have x->controller=bus y->target, no unit
        s += f",address.type=drive,address.controller={x},address.bus={x},address.target={y}"
        args.extend(["--disk", s])


    ### WRITE THE RESULTING DOMAIN XML ###
    xml_file = open(xml_name, 'w', encoding="utf-8")
    try:
        subprocess.run(args, stdout=xml_file, check=True, encoding='utf-8')
    except:
        print(" ".join(args))
        sys.exit(1)

    xml_file.close()


# detect virt-install version only considering major.minor
def detect_vinst_version() -> float:
    s: str = ""
    args: list = [ "virt-install", "--version" ]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        print(f"failed to detect virt-install version: {s}")
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    if (v < 2.2):
        print("virt-install version >= 2.2.0 is required for this command to work")
    if (v < 4.0):
        print("virt-install version >= 4.0.0 is recommended for best results")
    print(f"virt-install: detected version {v}")
    return v


def main(argc: int, argv: list) -> int:
    vinst_version : float = detect_vinst_version()
    if (argc < 2 or argc > 3):
        usage()
    vmx_name: str = argv[1]
    search_paths: list = [ os.path.dirname(vmx_name), "." ]
    if (argc > 2):
        search_paths.append(argv[2])

    vmx_file = open(vmx_name, 'r', encoding="utf-8")
    d : defaultdict = defaultdict(str)
    parse_vmx(vmx_file, d)
    vmx_file.close()

    name: str = d["displayname"]
    if (debug and name):
        print(f"[NAME] {name}")
    memory: int = int(d["memsize"] or 1024)
    if (debug and memory):
        print(f"[MEMORY] {memory}")

    genid: str = parse_genid(int(d["vm.genid"] or 0), int(d["vm.genidx"] or 0))
    if (debug and genid):
        print(f"[GENID] {genid}")

    # SMBIOS.reflectHost = "TRUE"
    # SMBIOS.noOEMStrings = "TRUE"
    # smbios.addHostVendor = "TRUE"
    sysinfo: str = "host" if (parse_boolean(d["smbios.reflectHost"])) else ""
    if (debug and sysinfo):
        print(f"[SYSINFO] {sysinfo}")

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
        print(f"[VCPUS] {vcpus},sockets={sockets},cores={cores},threads={threads}")

    cpu_model: str = "host" # most performant while still opening the door to migration
    iothreads: int = vcpus # XXX forgot the rule of thumb to set this

    uefi: str = ""
    if (d["firmware"] == "efi"):
        uefi = "uefi"
        if (vinst_version >= 4.0):
            if (parse_boolean(d["uefi.secureBoot.enabled"])):
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes"
            else:
                uefi += ",firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"

    if (debug and uefi):
        print(f"[UEFI] {uefi}")

    # ignore for now
    # guestos: str = parse_guestos(d["guestos"])

    svga: bool = parse_boolean(d["svga.present"])
    svga_memory: int = int(d["svgaram.vramSize"] or 0) // 1024
    vga: bool = parse_boolean(d["svga.vgaonly"])
    if (debug and vga):
        print(f"[VGA]")
    elif (debug and svga):
        print(f"[SVGA] {svga_memory}")

    sound: str = find_sound(d)
    if (debug and sound):
        print(f"[SOUND] {sound}")

    nvram: str = parse_filename(d["nvram"], search_paths)

    disk_ctrls: dict = { "scsi": {}, "sata": {}, "nvme": {}, "ide": {} }
    disks: list = []
    for interface in disk_ctrls:
        disk_ctrls[interface] = find_disk_controllers(d, interface)
        disks.extend(find_disks(d, search_paths, interface, disk_ctrls[interface]))

    floppys: dict = { 0: "", 1: "" }
    for i in range(2):
        floppys[i] = parse_filename(d[f"floppy{i}.filename"], search_paths)

    if (debug):
        print(disk_ctrls)
        print(disks)
        print(floppys)

    eths: list = find_eths(d, "ethernet")

    # run virt-install to generate the xml
    (xml_name, n) = re.subn("\.vmx$", ".xml", vmx_name, count=1, flags=re.IGNORECASE)
    if (n == 0):
        xml_name = vmx_name + ".xml"

    print(f"disk_ctrls={disk_ctrls}")
    virt_install(vinst_version, xml_name, vmx_name,
                 name, memory,
                 cpu_model,
                 vcpus, sockets, cores, threads,
                 iothreads,
                 genid, sysinfo,
                 uefi,
                 svga, svga_memory, vga,
                 sound,
                 nvram,
                 disk_ctrls,
                 disks,
                 floppys)
    return 0


sys.exit(main(len(sys.argv), sys.argv))
