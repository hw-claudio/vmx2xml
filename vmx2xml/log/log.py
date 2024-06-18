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
# Logging module

import logging
import typing

log: logging.Logger = logging.getLogger(__name__)

def log_disable_nl() -> None:
    global log
    handler: logging.StreamHandler = typing.cast(logging.StreamHandler, log.handlers[0])
    handler.terminator = ""


def log_enable_nl() -> None:
    global log
    handler: logging.StreamHandler = typing.cast(logging.StreamHandler, log.handlers[0])
    handler.terminator = "\n"


def log_init(verbose: int, quiet: int) -> None:
    global log
    loglevel: int = logging.WARNING - (verbose * 10) + (quiet * 10)
    log.setLevel(loglevel)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt='%(levelname)-8s| %(message)s'))
    log.addHandler(handler)


# get the verbosity level and quiet level from the loglevel
def log_get_vq() -> tuple:
    global log
    v: int = 0; q: int = 0

    if (log.level < logging.WARNING):
        v = (logging.WARNING - log.level) // 10
    if (log.level > logging.WARNING):
        q = (log.level - logging.WARNING) // 10
    return (v, q)
