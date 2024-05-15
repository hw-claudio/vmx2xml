#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# trivial stopwatch

import time
stopwatch_counter: float = 0.0


def stopwatch_start() -> None:
    global stopwatch_counter
    stopwatch_counter = time.perf_counter()


def stopwatch_elapsed() -> float:
    global stopwatch_counter
    return time.perf_counter() - stopwatch_counter
