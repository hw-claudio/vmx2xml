#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#

import configparser
import sys
import os
from os.path import join
from collections import defaultdict

def usage() -> None:
    print("usage: vmx2xml.py FILENAME.vmx [PATH_STORAGE]\n"
          "\n"
          "Convert a VMX Virtual Machine definition into a libvirt XML domain file,\n"
          "replacing all references to .vmdk to .qcow2\n"
          "\n"
          "PATH_STORAGE, if provided, is an additional path to search for referenced files.\n"
          "\n"
          "Searched PATHs:\n"
          "by default this command scans for referenced files in the same directory as\n"
          "FILENAME.vmx, then tries the current directory, then tries PATH_STORAGE and\n"
          "its subdirectories recursively if provided.\n\n")
    sys.exit(1)


def parse_boolean(s: str) -> bool:
    s = s.lower()
    if (s == "true"):
        return True
    else:
        return False


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


def parse_vmx(f, d: defaultdict):
    while (True):
        line: str = f.readline()
        if (line == ""):        # EOF
            return d
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



def find_disks(d: defaultdict, search_paths: tuple, interface: str):
    pathnames: list = []
    for x in range(10):
        for y in range(10):
            if not (parse_boolean(d[f"{interface}{x}:{y}.present"])):
                continue
            filename: str = d[f"{interface}{x}:{y}.filename"]
            if (filename != ""):
                print(f"{filename} => ", end="")
                pathname: str = find_file_ref(os.path.basename(filename), search_paths[0], False)
                if (pathname == ""):
                    pathname = find_file_ref(os.path.basename(filename), search_paths[1], False)
                if (pathname == "" and len(search_paths) == 3):
                    pathname = find_file_ref(os.path.basename(filename), search_paths[2], True)
                if (pathname == ""):
                    print(f"could not find in in {search_paths}")
                    sys.exit(1)
                print(f"{pathname}")
                pathnames.append(pathname)
    return pathnames


def main(argc: int, argv: list) -> int:
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

    domainname = d["displayname"]
    genid = d["vm.genid"]
    genidx = d["vm.genidx"]
    memory = d["memsize"]
    vcpus = d["numvcpus"]
    corespersocket = d["cpuid.corespersocket"]
    firmware = d["firmware"]
    sound = d["sound.virtualdev"]
    guestos = d["guestos"]
    nvram = d["nvram"]
    hpet = d["hpet0"]

    scsi: list = find_disks(d, search_paths, "scsi")
    nvme: list = find_disks(d, search_paths, "nvme")
    sata: list = find_disks(d, search_paths, "sata")
    ide: list = find_disks(d, search_paths, "ide")

    return 0


sys.exit(main(len(sys.argv), sys.argv))
