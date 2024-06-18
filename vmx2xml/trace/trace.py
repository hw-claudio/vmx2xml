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
