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
from vmx2xml.runcmd import *

def inspector_inspect(path: str) -> dict:
    s: str = runcmd([ "virt-inspector", "--no-icon", "--no-applications", "--echo-keys", path ], False)
    osd: dict = { "name": '', "osinfo": '' }

    if (not s):
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
    return runcmd_detectv([ "virt-inspector", "--version" ], r" (\d+\.\d+)", True)
