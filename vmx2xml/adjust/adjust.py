import sys
import os
import re
import subprocess

from vmx2xml.log import *

def adjust_guestfs(path: str, nbd: bool) -> bool:
    args: list = [ "adjust_guestfs.py", "-n" if (nbd) else "-f", path ]
    v: int; q: int; i: int

    (v, q) = log_get_vq()
    for i in range(v):
        args.append("-v")
    for i in range(q):
        args.append("-q")

    log.debug("%s", args)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    (s, _) = p.communicate()

    if (p.returncode != 0):
        return False
    return True


def adjust_guestfs_detect_version() -> float:
    s: str = ""
    args: list = [ "adjust_guestfs.py", "--version" ]

    log.debug("%s", args)
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, encoding='utf-8')
    except:
        log.critical("adjust_guestfs.py NOT FOUND")
        sys.exit(1)
    (s, _) = p.communicate()
    m = re.match(r"^(\d+\.\d+)", s)
    if not (m):
        log.critical("failed to detect adjust_guestfs.py version: %s", s)
        sys.exit(1)
    v: float = float(m.group(1)) or 0
    log.info("adjust_guestfs.py: detected version %s", v)
    return v
