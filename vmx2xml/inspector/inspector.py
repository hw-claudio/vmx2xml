#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# virt-inspector call to get os info on the image

import sys
import re
import subprocess

from vmx2xml.log import *
from vmx2xml.detectv import *

def inspector_inspect(path: str) -> dict:
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


def inspector_detect_version() -> float:
    return detectv([ "virt-inspector", "--version" ], r" (\d+\.\d+)", True)
