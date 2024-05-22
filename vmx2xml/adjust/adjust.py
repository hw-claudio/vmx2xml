#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Experimental guest adjustment submodule

import sys
import os
import re
import subprocess

from vmx2xml.log import *
from vmx2xml.runcmd import *


# in-place adjustment using virt-v2v-in-place, returns True on success.
def adjust_guestfs_v2v(from_file: str) -> bool:
    args: list = [ "virt-v2v-in-place", "--root=first", "-i", "disk" ]

    if (log.level > logging.WARNING):
        args.append("--quiet")
    if (log.level <= logging.DEBUG):
        args.append("-x")
    args.append(from_file)

    log.debug("%s", args)
    p = subprocess.run(args, stdout=subprocess.DEVNULL, encoding='utf-8')
    return (p.returncode == 0)


# adjust using the experimental adjust_guestfs.py. Returns True on success.
def adjust_guestfs_py(path: str, nbd: bool, adj_actions: dict) -> bool:
    args: list = [ "adjust_guestfs.py", "-n" if (nbd) else "-f", path ]
    if (adj_actions["drivers"]):
        args.append("-d")
    if (adj_actions["trim"]):
        args.append("-t")

    v: int; q: int; i: int
    (v, q) = log_get_vq()
    for i in range(v):
        args.append("-v")
    for i in range(q):
        args.append("-q")

    log.debug("%s", args)
    p = subprocess.run(args, encoding='utf-8', check=False)
    if (p.returncode != 0):
        return False
    return True


def adjust_guestfs(path: str, nbd: bool, adj_mode: str, adj_actions: dict) -> bool:
    if (adj_mode == "none"):
        log.warning('adjust_guestfs: unexpected call with adjustment mode "none"')
        return False
    log.info("adjust_guestfs: starting guest adjustment with method %s, actions %s", adj_mode, adj_actions)
    rv: bool
    if (adj_mode == "v2v"):
        rv = adjust_guestfs_v2v(path)
    elif (adj_mode == "x"):
        rv = adjust_guestfs_py(path, nbd, adj_actions)
    else:
        assert(0)               # unsupported adj_mode

    if (rv):
        log.info("adjust_guestfs: success adjusting disk %s", path)
    else:
        log.warning("adjust_guestfs: failure adjusting disk %s", path)
    return rv


def adjust_guestfs_detect_version() -> float:
    return runcmd_detectv([ "adjust_guestfs.py", "--version" ], r"^(\d+\.\d+)", True)
