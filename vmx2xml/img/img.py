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
# Image conversion submodule

import sys
import os
import re
import subprocess
import tempfile
import glob
import time

from vmx2xml.log import log, logging
from vmx2xml.numa import numa_restrict_cmd
from vmx2xml.trace import trace_cmd_start
from vmx2xml.adjust import adjust_guestfs
from vmx2xml.runcmd import runcmd


def img_wait_child(pid: int) -> None:
    try:
        os.waitpid(pid, 0)
    except:
        pass


def img_file_ext(raw: bool) -> str:
    return "raw" if (raw) else "qcow2"


def img_v2v_convert(from_file: str, to_file: str, trace_cmd: bool, numa_node: int, raw: bool) -> None:
    to_file_ext: str = img_file_ext(raw)
    dirname: str = os.path.dirname(to_file)
    args: list = []

    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-v2v.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))
    args.extend(["virt-v2v", "--root=first", "-i", "disk", "-o", "disk", "-of", to_file_ext, "-os", dirname])

    if (log.level > logging.WARNING):
        args.append("--quiet")
    if (log.level <= logging.DEBUG):
        args.append("-x")
    args.append(from_file)

    log.debug("%s", args)
    p = subprocess.run(args, stdout=subprocess.DEVNULL, encoding='utf-8')

    if (p.returncode == 0):
        log.info("virt-v2v: reports success converting disk %s", from_file)
    else:
        log.warning("virt-v2v: reports failure converting disk %s", from_file)

    if (trace_cmd):
        os.kill(tpid, 2)
        img_wait_child(tpid)

    # Now rename to the name we want
    srcnames: list = glob.glob(to_file[0:-len(f".{to_file_ext}")] + "-sd*")
    if (len(srcnames) != 1):
        log.critical("could not find the generated disk %s", to_file)
        sys.exit(1)
    os.rename(srcnames[0], to_file)


# there is no annotation for Tempfile, so return type is unknown
def img_qemu_create_overlay(from_file: str, bformat: str):
    tmp = tempfile.NamedTemporaryFile()
    args: list = ["qemu-img", "create", "-b", from_file, '-F', bformat, '-f', 'qcow2']
    if (log.level > logging.DEBUG):
        args.append("-q")
    args.append(tmp.name)
    log.debug("%s", args)
    _ = subprocess.run(args, stdout=sys.stderr, check=True)
    return tmp


def img_qemu_create(to_file: str, vsize: int, raw: bool) -> None:
    to_file_ext: str = img_file_ext(raw)
    args: list = ["qemu-img", "create", "-f", to_file_ext]
    if (log.level > logging.DEBUG):
        args.append("-q")
    args.extend([to_file, str(vsize)])
    log.debug("%s", args)
    _ = subprocess.run(args, stdout=sys.stderr, check=True)


def img_qemu_copy(from_file: str, to_file: str, trace_cmd: bool, cache_mode: str,
                  numa_node: int, parallel: int, raw: bool) -> None:
    to_file_ext: str = img_file_ext(raw)
    args: list = []
    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-qemu-img.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))
    args.extend(["qemu-img", "convert", "-O", to_file_ext, "-t", cache_mode, "-T", cache_mode])
    if (parallel > 0):
        args.extend(["-m", str(parallel)])
    if (log.getEffectiveLevel() <= logging.WARNING):
        args.append("-p")
    args.extend([from_file, to_file])

    log.debug("%s", args)
    _ = subprocess.run(args, check=True)
    if (trace_cmd):
        os.kill(tpid, 2)
        img_wait_child(tpid)


def img_qemu_info(from_file: str) -> int:
    s: str = runcmd(["qemu-img", "info", "-U", from_file], True)
    # sorry, human output is way easier to parse than the json file.
    vsize_m = re.search(r"^virtual size:.*\((\d+) bytes\)", s, flags=re.MULTILINE)
    if (not vsize_m):
        log.critical("qemu-img info output could not be parsed!")
        sys.exit(1)
    return int(vsize_m.group(1))


def img_qemu_convert(sourcepath: str, targetpath: str, adj_mode: str, adj_actions: dict,
                     trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, raw: bool) -> None:
    src: str = sourcepath
    if (adj_mode != "none"):
        tmp = img_qemu_create_overlay(sourcepath, "vmdk")
        adjust_guestfs(tmp.name, False, adj_mode, adj_actions)
        src = tmp.name

    img_qemu_copy(src, targetpath, trace_cmd, cache_mode, numa_node, parallel, raw)
    if (adj_mode != "none"):
        tmp.close()


def img_qemu_nbd_create(s: str, overlay: bool, cache_mode: str, raw: bool, readonly: bool) -> tuple:
    tmp = tempfile.NamedTemporaryFile(delete=False)
    args: list = ["qemu-nbd", f"--cache={cache_mode}", "-t", "--shared=0", "--discard=unmap", "--socket", tmp.name]
    if (raw):
        args.extend(['-f', 'raw'])
    if (overlay):
        args.append("-s")
    if (readonly):
        args.append("-r")
    args.append(s)
    log.debug("%s", args)
    pid: int = os.fork()
    if (pid == 0):
        os.execvp(args[0], args)
    args = ["nbdinfo", f"nbd+unix:///?socket={tmp.name}"]
    while True:
        log.debug("%s", args)
        p = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if (p.returncode == 0):
            break
        time.sleep(1)
    return (tmp, pid)


def img_qemu_nbd_copy(sin: str, sout: str, trace_cmd: bool, numa_node: int, parallel: int) -> None:
    args: list = []
    if (trace_cmd):
        tpid: int = trace_cmd_start("trace-nbdcopy.dat-", numa_node)
    if (numa_node >= 0):
        args.extend(numa_restrict_cmd(numa_node))

    args.extend(["nbdcopy", f"nbd+unix:///?socket={sin}", f"nbd+unix:///?socket={sout}",
                 '--requests=64', '--flush', '--progress'])
    if (parallel > 0):
        args.extend(['-C', str(parallel), '-T', str(parallel)])
    log.debug("%s", args)

    _ = subprocess.run(args, check=True)
    if (trace_cmd):
        os.kill(tpid, 2)
        img_wait_child(tpid)


def img_qemu_nbd_convert(sourcepath: str, targetpath: str, adj_mode: str, adj_actions: dict,
                         trace_cmd: bool, cache_mode: str, numa_node: int, parallel: int, raw: bool) -> None:
    (sin, pidin) = img_qemu_nbd_create(sourcepath, adj_mode != "none", cache_mode, False, adj_mode == "none")
    if (adj_mode != "none"):
        adjust_guestfs(sin.name, True, adj_mode, adj_actions)
    vsize: int = img_qemu_info(sourcepath)
    img_qemu_create(targetpath, vsize, raw)
    (sout, pidout) = img_qemu_nbd_create(targetpath, False, cache_mode, raw, False)
    img_qemu_nbd_copy(sin.name, sout.name, trace_cmd, numa_node, parallel)
    sin.close()
    sout.close()
    os.remove(sin.name)
    os.remove(sout.name)
    os.kill(pidin, 15)
    os.kill(pidout, 15)
