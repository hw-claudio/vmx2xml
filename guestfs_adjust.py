#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# This tool adjust the guest to run on KVM.
# It is a simplified version of the virt-v2v convert_linux and convert_windows
# parts, containing only the fundamentals and working in place without overlays
# for performance reasons, something that is broken on virt-v2v.

import sys
import os.path
import argparse
import logging
import guestfs

log: logging.Logger = logging.getLogger(__name__)
program_version: str = "0.1"

def log_disable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = ""


def log_enable_nl() -> None:
    global log
    handler: logging.StreamHandler = log.handlers[0]
    handler.terminator = "\n"


# Launches libguestfs, and attempts to detect a supported guestOS to adjust.
# If it finds a supported OS, returns a tuple (GuestFS, rootdev, supported_os),
# otherwise it returns (None, None, None)
#
# Es: (g, "/dev/sda2", "linux")
#
def guestfs_launch(path: str, nbd: bool) -> tuple:
    try:
        g: guestfs.GuestFS = guestfs.GuestFS(python_return_dict=True)
        if (log.level <= logging.DEBUG):
            g.set_trace(1)
        if (not nbd):
            g.add_drive_opts(path, format="qcow2", discard="besteffort", cachemode="unsafe")
        else:
            srv: str = f"unix:{path}"
            g.add_drive_opts("", format="raw", protocol="nbd", server=[srv], discard="besteffort", cachemode="unsafe")
        g.launch()
        os_type: str = ""
        root: str = ""
        roots: list = g.inspect_os()
        if (not roots):
            return (None, None, None)
        for i in range(len(roots)):
            root = roots[i]
            os_type = g.inspect_get_type(root)
            if (os_type == "linux" or os_type == "windows"):
                return (g, root, os_type)
        return (None, None, None)
    except RuntimeError as err:
        log.error("libguestfs failed to run a command: %s", err)
        return (None, None, None)


def get_initrd_prg(g: guestfs.GuestFS, prg: str) -> str:
    dirs: list = ["/sbin", "/usr/sbin", "/bin", "/usr/bin"]
    for dir in dirs:
        name: str = f"{dir}/{prg}"
        if (g.is_file(name, followsymlinks=True)):
            return name
    return ""


def guestfs_lin_mount_all(g: guestfs.GuestFS, root: str) -> bool:
    # mount the root directory and get all mountpoints
    try:
        g.mount(root, "/")
        mountpoints: dict = g.inspect_get_mountpoints(root)
    except RuntimeError as err:
        log.error("libguestfs failed to run a command: %s", err)
        return False

    # now mount all mountpoints detected
    try:
        for key in mountpoints:
            if (key == root or mountpoints[key] == "/"):
                continue        # we already mounted root
            g.mount(mountpoints[key], key)
    except RuntimeError as err:
        log.debug("failed to mount: %s, ignoring.", err)

    return True


def guestfs_lin_trim_all(g: guestfs.GuestFS) -> bool:
    # mount all the filesystems and trim them
    try:
        filesystems: dict = g.list_filesystems()
    except RuntimeError as err:
        log.warning("%s", err)
        return False

    for fs in filesystems:
        if (filesystems[fs] == "swap"):
            continue
        try:
            g.umount_all()
            g.mount_options("discard", fs, "/")
            # XXX maybe also g.zero_free_space() before that? What's the impact on large VMs?
            g.fstrim("/")
        except RuntimeError as err:
            log.info("%s, ignoring.", err)

    return True


def guestfs_lin_update_initrd(g: guestfs.GuestFS) -> bool:
    # look for the currently used initrd and the kernel version
    link: str = ""; initrd: str = ""; version: str = ""
    # try to use symlinks from /boot/vmlinuz, /boot/initrd and /boot/initrd.img
    # to determine the version part of the currently used initrd filename
    links: list = [ "vmlinuz", "initrd", "initrd.img", "initramfs", "initramfs.img", "config", "System.map" ]
    target: str = ""; matches: list

    for link in links:
        try:
            target = g.readlink(f"/boot/{link}")
            if not (os.path.isabs(target)):
                target = os.path.normpath(os.path.join("/boot", target))
            break
        except RuntimeError:
            log.info("no /boot/%s link found", link)
    if (target):
        version = target[len(f"/boot/{link}") + 1:]

    if (not version):
        # we could not get version from a link, try from /lib/modules/version
        matches = g.glob_expand("/lib/modules/*")
        if (len(matches) == 1):
            target = matches[0]
            version = target[len(f"/lib/modules/"):-1] # strip the final / added by glob to dirs
    if (not version):
        # try from unique entries of links
        for link in links:
            matches = g.glob_expand(f"/boot/{link}-*")
            if (len(matches) == 1):
                target = matches[0]
                version = target[len(f"/boot/{link}-"):]
    if (version):
        # finally we got a version
        log.debug("version %s detected", version)
        # try to find an initrd file that is named after the version
        try:
            glob: str = f"/boot/initrd*-{version}"
            matches = g.glob_expand(glob)
            if (len(matches) != 1):
                glob = f"/boot/initrd*-{version}.img"
                matches = g.glob_expand(glob)
            if (len(matches) != 1):
                glob = f"/boot/initramfs*-{version}"
                matches = g.glob_expand(glob)
            if (len(matches) != 1):
                glob = f"/boot/initramfs*-{version}.img"
                matches = g.glob_expand(glob)
            if (len(matches) != 1):
                raise RuntimeError("no unique initrd match")
            initrd = matches[0]
        except RuntimeError as err:
            log.info("could not find initrd for version %s: %s", version, err)

    if (not initrd):
        # everything has failed, try getting the first inird we see
        try:
            matches = g.glob_expand("/boot/initrd*")
            if (len(matches) < 1):
                matches = g.glob_expand("/boot/initramfs*")
                if (len(matches) < 1):
                    raise RuntimeError("no initrd match")
            if (len(matches) > 1):
                log.warning("matching the first initrd found and crossing fingers!")
            initrd = matches[0]
        except RuntimeError:
            log.error("could not find initrd as a last resort by globbing initrd*")
            return False

    assert(initrd)
    # now detect which tool to use to update this initrd

    # try make-initrd, which should automatically install virtio stuff when virtualized
    sbin: str = get_initrd_prg(g, "make-initrd")
    if (sbin):
        try:
            g.command([sbin, "-k", version])
            return True
        except RuntimeError as err:
            log.error("make-initrd failed: %s", err)
            return False

    # try dracut
    sbin = get_initrd_prg(g, "dracut")
    if (sbin):
        try:
            g.command([sbin, "--force", "--add-drivers", "virtio_pci virtio_scsi virtio_blk", initrd, version])
            return True
        except RuntimeError as err:
            log.error("dracut failed: %s", err)
            return False

    sbin = get_initrd_prg(g, "update-initramfs")
    if (sbin):
        try:
            g.write_append("/etc/initramfs-tools/modules", "\nvirtio_pci\nvirtio_scsi\nvirtio_blk\n")
            g.command([sbin, "-c", "-k", version])
            return True
        except RuntimeError as err:
            log.error("update-initramfs failed: %s", err)
            return False

    # try mkinitrd
    sbin = get_initrd_prg(g, "mkinitrd")
    if (sbin):
        try:
            g.command([sbin, "--with=virtio_pci", "--with=virtio_scsi", "--with=virtio_blk", initrd, version])
            return True
        except RuntimeError as err:
            log.error("mkinitrd failed: %s", err)
            return False
    # nothing worked
    log.error("did not find any supported tool to update initrd")
    return False


def guestfs_lin(g: guestfs.GuestFS, root: str) -> bool:
    if not (guestfs_lin_mount_all(g, root)):
        return False
    if not (guestfs_lin_update_initrd(g)):
        return False
    if not (guestfs_lin_trim_all(g)):
        return False
    return True


def guestfs_win(g: guestfs.GuestFS, root: str) -> bool:
    return False


def guestfs_adjust(path: str, nbd: bool) -> bool:
    g: guestfs.GuestFS; root: str; os_type: str
    (g, root, os_type) = guestfs_launch(path, nbd)
    if (not g):
        log.warning("could not detect any supported guestOS in %s,\n"
                    "it will be left untouched.", path)
        return False
    rv: bool
    if (os_type == "linux"):
        rv = guestfs_lin(g, root)
    elif (os_type == "windows"):
        rv = guestfs_win(g, root)
    else:
        rv = False # supported OS must be handled before reaching here
    g.close()
    return rv


def get_options(argc: int, argv: list) -> tuple:
    global log
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='guestfs_adjust.py',
        description="does the minimal adjustments to get a guest image to run on KVM\n",
        epilog="requires libguestfs, including libguestfs-winsupport"
    )
    parser.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    parser.add_argument('-V', '--version', action='version', version=program_version)
    parser.add_argument('-f', '--filename', metavar="IMGFILE", action='store',
                        help='the guest image to be converted')
    parser.add_argument('-n', '--nbd', metavar="NAMEDSOCKET", action='store',
                        help='an NBD socket to use instead of filename')

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    if (not args.filename) and (not args.nbd):
        log.critical("specify either -f, --filename or -n, --nbd.")
        sys.exit(1)
    if (args.filename and args.nbd):
        log.critical("cannot specify both -f, --filename and -n, --nbd at the same time.")
        sys.exit(1)
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2
    loglevel: int = logging.WARNING - (args.verbose * 10) + (args.quiet * 10)

    log.setLevel(loglevel)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt='%(message)s'))
    log.addHandler(handler)

    filename: str = args.filename
    nbd: str = args.nbd
    log.debug("[OPTIONS] filename=%s nbd=%s", filename, nbd)
    return (filename, nbd)


def main(argc: int, argv: list) -> int:
    (filename, nbd) = get_options(argc, argv)
    rv: bool

    if (filename):
        rv = guestfs_adjust(filename, False)
    else:
        rv = guestfs_adjust(nbd, True)

    if (rv):
        log.warning("guest adjustment successful.")
        return 0
    else:
        log.warning("guest adjustment reports failure.")
        return 1


sys.exit(main(len(sys.argv), sys.argv))
