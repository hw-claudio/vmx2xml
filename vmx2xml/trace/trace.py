#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Tracing submodule

import sys
import os
import re
import tempfile

from vmx2xml.log import *
from vmx2xml.numa import *
from vmx2xml.runcmd import *

def trace_cmd_start(pre: str, numa_node: int) -> int:
    args: list = []
    if (numa_node >= 0):
        # run trace-cmd on the other non-selected node
        args.extend(numa_restrict_cmd(0 if (numa_node > 0) else 1))
    tmp = tempfile.NamedTemporaryFile(delete=False, prefix=pre)
    args.extend([ "trace-cmd", "record", "-o", tmp.name, "-e", "sched", "-e", "syscalls", "-e", "irq" ])
    if (log.level > logging.DEBUG):
        args.append("-q")
    log.debug("%s", args)

    pid: int = os.fork()
    if (pid == 0):
        os.execvp(args[0], args)
    return pid


def trace_cmd_detect_version() -> float:
    v: float = runcmd_detectv([ "trace-cmd", "-h" ], r"^.*version (\d+\.\d+)", True)
    if (v < 2.7):
        log.critical("trace-cmd >= 2.7 required")
        sys.exit(1)
    return v
