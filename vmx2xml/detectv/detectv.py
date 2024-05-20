#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# command version detection submodule

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


def detectv(args: list, r: str, check: bool) -> float:
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
