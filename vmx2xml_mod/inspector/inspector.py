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
# virt-inspector call to get os info on the image

import re

from vmx2xml_mod.log import log
from vmx2xml_mod.runcmd import runcmd, runcmd_detectv

def inspector_inspect(path: str) -> dict:
    s: str = runcmd(["virt-inspector", "--no-icon", "--no-applications", "--echo-keys", path], False)
    osd: dict = {"name": '', "osinfo": ''}

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
    return runcmd_detectv(["virt-inspector", "--version"], r" (\d+\.\d+)", True)
