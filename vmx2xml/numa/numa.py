#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# NUMA functions submodule

def numa_restrict_cmd(numa_node: int) -> list:
    return [ "numactl", "-m", str(numa_node), "-N", str(numa_node), "--" ]
