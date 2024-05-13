#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Logging module

import logging

log: logging.Logger = logging.getLogger(__name__)

def log_disable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = ""


def log_enable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = "\n"


def log_init(verbose: int, quiet: int) -> None:
    global log
    loglevel: int = logging.WARNING - (verbose * 10) + (quiet * 10)
    log.setLevel(loglevel)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt='%(message)s'))
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
