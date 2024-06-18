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
# Command execution and version detection submodule

import sys
import re
import subprocess

from vmx2xml.log import *


def detectv_failed(arg: str, check: bool, e: str) -> float:
    if (check):
        log.critical("%s: failed to %s", arg, e)
        sys.exit(1)
    log.warning("%s: failed to %s", arg, e)
    return 0


def runcmd_detectv(args: list, r: str, check: bool) -> float:
    s: str = ""
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        return detectv_failed(args[0], check, "run")
    (s, _) = p.communicate()
    m = re.search(r, s, flags=re.MULTILINE)
    if not (m):
        return detectv_failed(args[0], check, "detect version")
    v: float = float(m.group(1)) or 0
    if (v == 0):
        return detectv_failed(args[0], check, "parse version")
    log.info("%s: detected version %s", args[0], v)
    return v


def runcmd(args: list, check: bool) -> str:
    exp_str: str
    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
    except Exception as exp:
        exp_str = re.sub("\s", " ", str(exp), count=0, flags=0)
        log.critical("%s: exception running command %s: %s", args[0], args, exp_str)
        sys.exit(1)
    (s, e) = p.communicate()
    if (p.returncode != 0):
        exp_str = re.sub("\s", " ", e, count=0, flags=0)
        if (check):
            log.critical("%s: failure detected in command %s: %s", args[0], args, exp_str)
            sys.exit(1)
        log.warning("%s: failure detected in command %s: %s", args[0], args, exp_str)
        return ""
    return s
