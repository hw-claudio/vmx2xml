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
# Just a demo for the VMWare to KVM conversion
#
# requires GTK3, python-gtk and Python psutil.

import os
import sys
import glob
import re
import argparse
import concurrent.futures
import functools

import psutil
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from vmx2xml.log import log, logging, log_init
from vmx2xml.runcmd import runcmd


program_version: str = "0.1"
border: int = 24
spacing_v: int = 24
pulse_timer: int = 200
progress_timer: int = 3000
test_datastore: str = "/vm_testboot"
test_executors: dict = {}
migrate_executors: dict = {}
test_ok_str: str = "Tested!"
migrate_ok_str: str = "Migrated!"
success_str: str = "SUCCESS"

# MAIN WINDOW
w: Gtk.Window

vm_find_button: Gtk.Button
src_tree_store: Gtk.TreeStore; src_tree_view: Gtk.TreeView
external_button: Gtk.MenuButton; networks_button: Gtk.MenuButton
test_arrow: Gtk.Button
test_tree_store: Gtk.TreeStore; test_tree_view: Gtk.TreeView; test_cancel_button: Gtk.Button
tgt_arrow: Gtk.Button
tgt_tree_store: Gtk.TreeStore; tgt_tree_view: Gtk.TreeView; restart_button: Gtk.Button

# EXTERNAL WINDOW
external_window: Gtk.Popover
external_tree_store: Gtk.TreeStore; external_tree_view: Gtk.TreeView

# NETWORKS WINDOW
networks_window: Gtk.Popover
networks_tree_store: Gtk.TreeStore; networks_tree_view: Gtk.TreeView

def get_folder_size_str(f: str) -> str:
    size_str: str = runcmd(["du", "-s", "-h", f], True)
    size_str = size_str.strip()
    m = re.match(r"^(\S+)\s+", size_str)
    if (m):
        return m.group(1)
    log.error("get_folder_size_str: failed to match input %s", size_str)
    return ""


def get_folder_avail_str(f: str) -> str:
    size_str: str = runcmd(["df", "-h", "--output=avail", f], True)
    size_str = size_str.strip()
    lines = size_str.splitlines()
    return lines[1]             # skip the header line


# get the target xmlpath from the input vmxpath and target datastore
def get_xmlpath_from_vmx(vmxpath: str, ds_tgt: str) -> str:
    ds_src: str = os.path.dirname(os.path.dirname(vmxpath))
    xmlpath: str = vmxpath.replace(ds_src, ds_tgt, 1)
    (match, is_vmx) = re.subn(r"\.vmx$", ".xml", xmlpath, count=1, flags=re.IGNORECASE)
    if (is_vmx != 1):
        log.error("get_xmlpath_from_vmx: is_vmx not 1")
        return ""
    xmlpath = match
    return xmlpath


def convert_progress_idle(vmxpath: str, xmlpath: str, t: Gtk.TreeStore, executors: dict, progress_f) -> bool:
    row: Gtk.TreeModelRow = tree_store_search(t, vmxpath, 3)
    if not (row):
        return False
    # increase spinner
    if (row[6] >= 0):
        row[6] += 1
    try:
        f = open(xmlpath + ".prg", "rb")
        f.seek(-15, os.SEEK_END)
        b: bytes = f.read()
    except:
        return False
    if (len(b) < 14):
        return False

    if (row[6] >= 0):
        row[5] = 0
        row[6] = -1
        row[1] = "Converting..."
        GLib.source_remove(executors[vmxpath]["timer"])
        executors[vmxpath]["timer"] = GLib.timeout_add(progress_timer, progress_f, vmxpath, xmlpath)

    txt: str = b.decode("ascii")
    log.debug("convert_progress: %s read: %s", xmlpath, txt)
    m = re.match(r"\s*\((\d+)\.\d\d/100%\)\r\n*", txt)
    f.close()
    if (not m):
        return False
    row[5] = int(m.group(1))
    row[1] = f"Converting ({row[5]}%)"
    return False


def arrow_pressed(b: Gtk.Button, _e: Gdk.EventButton) -> bool:
    log.debug("arrow_pressed! b=%s", b)
    arrow_light = Gtk.Image.new_from_file("art/arrow_light.png")
    b.set_image(arrow_light)
    return False


def arrow_clicked(b: Gtk.Button):
    arrow_dark = Gtk.Image.new_from_file("art/arrow_dark.png")
    b.set_image(arrow_dark)
    if (b == test_arrow):
        test_arrow_clicked(b)
    elif (b == tgt_arrow):
        tgt_arrow_clicked(b)


def arrow_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button()
    arrow_dark = Gtk.Image.new_from_file("art/arrow_dark.png")
    b.set_image(arrow_dark)
    b.set_always_show_image(True)
    b.connect("button-press-event", arrow_pressed)
    b.connect("clicked", arrow_clicked)
    return b


def header_suse_init() -> Gtk.Image:
    i: Gtk.Image = Gtk.Image.new_from_file("art/suse-logo-small-h.png")
    return i


def header_title_init() -> Gtk.Label:
    l: Gtk.Label = Gtk.Label(label="Convert to KVM!")
    c = l.get_style_context()
    c.add_class("title")
    return l


def header_kvm_init() -> Gtk.Image:
    i: Gtk.Image = Gtk.Image.new_from_file("art/kvm-logo.png")
    return i


def vm_entry_init() -> Gtk.Entry:
    e: Gtk.Entry = Gtk.Entry()
    e.set_text("Find VMs >>>")
    e.set_editable(False)
    e.set_alignment(1)
    return e


def tree_store_init() -> Gtk.TreeStore:
    s: Gtk.TreeStore = Gtk.TreeStore(str, str, str, str, str, int, int)
    return s


def tree_store_search_children(t: Gtk.TreeStore, row: Gtk.TreeModelRow, s: str, i: int) -> Gtk.TreeModelRow:
    it: Gtk.TreeIter = row.iter
    child_it: Gtk.TreeIter = t.iter_children(it)
    while (child_it):
        if (s == t[child_it][i]):
            return t[child_it]
        child_it = t.iter_next(child_it)
    return None


def tree_store_search(t: Gtk.TreeStore, s: str, i: int) -> Gtk.TreeModelRow:
    for row in t:
        if (s == row[i]):
            return row
        child_row = tree_store_search_children(t, row, s, i)
        if (child_row):
            return child_row
    return None


def src_tree_store_add(t: Gtk.TreeStore, root: str, vms: list) -> None:
    size_str: str = get_folder_size_str(root)
    it: Gtk.TreeIter = t.append(None, [os.path.basename(root), size_str, "", root, "", 0, -1])
    for vm in vms:
        size_str = get_folder_size_str(os.path.dirname(vm["path"]))
        t.append(it, [vm["name"], size_str, "", vm["path"], "", 0, -1])
    external_rescan(None)
    networks_rescan(None)


def tgt_tree_store_add(t: Gtk.TreeStore, root: str) -> None:
    avail_str: str = get_folder_avail_str(root)
    t.append(None, [os.path.basename(root), avail_str, "", root, "", 0, -1])


def src_tree_store_walk(t: Gtk.TreeStore, folder: str) -> None:
    for (root, dirs, _files) in os.walk(folder, topdown=True):
        if (tree_store_search(t, root, 3)):
            continue
        vms: list = []; i: int = 0
        for this in dirs:
            names: list = glob.glob(os.path.join(root, this, "*.vmx"))
            count = len(names)
            for i in range(0, count):
                vms.append({"name": this, "path": names[i]})
        if (len(vms) >= 1):
            src_tree_store_add(t, root, vms)


def src_tree_view_activated(_view: Gtk.TreeView, p: Gtk.TreePath, _c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = src_tree_store
    ds_chooser = Gtk.FileChooserDialog(title="Select or Create target datastore folder")
    ds_chooser.set_create_folders(True)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        if not (tree_store_search(tgt_tree_store, f, 3)):
            tgt_tree_store_add(tgt_tree_store, f)
        it: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[it][2] = ds
        t[it][4] = f
        child_it: Gtk.TreeIter = t.iter_children(it)
        while (child_it):
            t[child_it][2] = ds
            t[child_it][4] = f
            child_it = t.iter_next(child_it)
    ds_chooser.destroy()


def tree_view_edited(_cell: Gtk.CellRendererText, pathstr: str, newtxt: str, data: tuple):
    tree_store: Gtk.TreeStore = data[0]
    i: int = data[1]
    it: Gtk.TreeIter = tree_store.get_iter_from_string(pathstr)
    tree_store[it][i] = newtxt


def tree_view_row_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    if (view == src_tree_view):
        return src_tree_view_activated(view, p, c)
    if (view == external_tree_view and (c == view.get_column(1))):
        return external_tree_view_src_activated(view, p, c)
    if (view == external_tree_view and (c == view.get_column(2))):
        return external_tree_view_tgt_activated(view, p, c)
    if (view == networks_tree_view and (c == view.get_column(0))):
        return networks_tree_view_src_activated(view, p, c)
    if (view == networks_tree_view and (c == view.get_column(1))):
        return networks_tree_view_tgt_activated(view, p, c)
    return None


def tree_view_init(tree_store: Gtk.TreeStore, layout: Gtk.Layout,
                   columns: list, csizes: list, rend: list) -> Gtk.TreeView:
    view: Gtk.TreeView = Gtk.TreeView(model=tree_store)

    for i in range(len(columns)):
        renderer: Gtk.CellRenderer
        c: Gtk.TreeViewColumn
        if (rend[i] == 2):
            renderer = Gtk.CellRendererProgress()
            c = Gtk.TreeViewColumn(columns[i], renderer, text=i, value=5, pulse=6)
        elif (rend[i] == 1):
            renderer = Gtk.CellRendererText()
            renderer.set_property("editable", True)
            renderer.connect("edited", tree_view_edited, (tree_store, i))
            c = Gtk.TreeViewColumn(columns[i], renderer, text=i)
        else:
            renderer = Gtk.CellRendererText()
            c = Gtk.TreeViewColumn(columns[i], renderer, text=i)
        c.set_min_width(csizes[i])
        c.set_max_width(csizes[i])
        c.set_fixed_width(csizes[i])
        c.set_resizable(False)
        c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        c.set_expand(True)
        view.append_column(c)
    view.connect("row-activated", tree_view_row_activated)
    selection: Gtk.TreeSelection = view.get_selection()
    selection.set_mode(Gtk.SelectionMode.MULTIPLE)
    tree_view_scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow()
    tree_view_scroll.add(view)
    tree_view_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
    layout.pack_start(tree_view_scroll, True, True, 0)
    return view


def vm_find_button_clicked(_w: Gtk.Widget):
    vm_chooser = Gtk.FileChooserDialog(title="Select Folder to scan for VMX files")
    vm_chooser.set_create_folders(False)
    vm_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    vm_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = vm_chooser.run()

    if (response == Gtk.ResponseType.OK):
        src_tree_store_walk(src_tree_store, vm_chooser.get_filename())
    vm_chooser.destroy()


def vm_find_button_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Find")
    b.connect("clicked", vm_find_button_clicked)
    return b


def restart_button_clicked(widget: Gtk.Widget):
    src_tree_store.clear()
    tgt_tree_store.clear()

    test_cancel_button_clicked(widget)

    external_tree_store.clear()
    networks_tree_store.clear()

    # kill lingering child processes from tests and migrations
    kill_child_processes(os.getpid())


def restart_button_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Restart")
    b.connect("clicked", restart_button_clicked)
    return b


def kill_child_processes(parent_pid):
    try:
        parent = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        return
    children = parent.children(recursive=True)
    for process in children:
        log.debug("killing child: %s", process)
        os.kill(process.pid, 15)


def test_cancel_button_clicked(_w: Gtk.Widget):
    global test_executors
    for vmxpath in test_executors:
        test_executors[vmxpath]["executor"].shutdown(wait=False)
        if (test_executors[vmxpath]["timer"] >= 0):
            GLib.source_remove(test_executors[vmxpath]["timer"])
            test_executors[vmxpath]["timer"] = -1
    # XXX there could be lingering children processes
    # Using the restart button once in a while will be good to clean up all children
    #kill_child_processes(os.getpid())
    test_executors = {}
    test_tree_store.clear()


def test_cancel_button_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Cancel All Tests")
    b.connect("clicked", test_cancel_button_clicked)
    return b


def ds_label_init(text: str) -> Gtk.Label:
    l: Gtk.Label = Gtk.Label(label=text)
    c = l.get_style_context()
    c.add_class("ds_label")
    return l


def test_vm_boot_complete_end(result_str: str, vmxpath: str, _xmlpath: str) -> bool:
    global test_executors
    if vmxpath in test_executors:
        test_executors[vmxpath]["executor"].shutdown(wait=False)
        if (test_executors[vmxpath]["timer"] >= 0):
            GLib.source_remove(test_executors[vmxpath]["timer"])
            test_executors[vmxpath]["timer"] = -1
        del test_executors[vmxpath]

    row: Gtk.TreeModelRow = tree_store_search(test_tree_store, vmxpath, 3)
    if not (row):
        log.warning("test_vm_complete_update: %s: not found in test_tree_store", vmxpath)
        return False
    row[2] = result_str
    if (result_str == success_str):
        row[1] = test_ok_str
        row[5] = 100
        row[6] = -1
    else:
        row[5] = 0
        row[6] = -1
    return False


def test_vm_boot_complete(vmxpath: str, xmlpath: str, future: concurrent.futures.Future) -> None:
    try:
        result_str = future.result()
    except Exception as e:
        log.error("test_vm_boot_complete exception: %s", ''.join(str(e).splitlines()))
        result_str = "ERROR"
    GLib.idle_add(test_vm_boot_complete_end, result_str, vmxpath, xmlpath)


def test_vm_boot(name: str, xmlpath: str) -> str:
    args: list = ["demo_test_boot.sh", name, xmlpath]
    log.debug("%s", args)
    result_str: str = runcmd(args, True)
    result_str = result_str.strip()
    log.info("test_vm_boot: %s", result_str)
    return result_str


def test_vm_boot_progress_idle(vmxpath: str, _xmlpath: str) -> bool:
    row: Gtk.TreeModelRow = tree_store_search(test_tree_store, vmxpath, 3)
    if not (row):
        return False
    # increase spinner
    if (row[6] >= 0):
        row[6] += 1
    return False


def test_vm_boot_progress(vmxpath: str, xmlpath: str) -> bool:
    log.debug("test_vm_boot_progress timer triggered")
    GLib.idle_add(test_vm_boot_progress_idle, vmxpath, xmlpath)
    return True


def test_vm_convert_complete_next(result_str: str, vmxpath: str, xmlpath: str) -> bool:
    global test_executors
    if vmxpath in test_executors:
        test_executors[vmxpath]["executor"].shutdown(wait=False)
        if (test_executors[vmxpath]["timer"] >= 0):
            GLib.source_remove(test_executors[vmxpath]["timer"])
            test_executors[vmxpath]["timer"] = -1
        del test_executors[vmxpath]

    row: Gtk.TreeModelRow = tree_store_search(test_tree_store, vmxpath, 3)
    if not (row):
        log.warning("test_vm_complete_update: %s: not found in test_tree_store", vmxpath)
        return False
    if (result_str != success_str):
        row[2] = result_str
        row[5] = 0
        row[6] = -1
        return False
    row[2] = ""
    row[5] = 0
    row[6] = 0
    row[1] = "Booting..."
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    #assert(row[3] == vmxpath)
    #assert(row[4] == xmlpath)
    future: concurrent.futures.Future = executor.submit(test_vm_boot, row[0], row[4])
    timer = GLib.timeout_add(pulse_timer, test_vm_boot_progress, vmxpath, xmlpath)
    test_executors[vmxpath] = {"executor": executor, "timer": timer}
    future.add_done_callback(functools.partial(test_vm_boot_complete, vmxpath, xmlpath))
    return False


def test_vm_convert_complete(vmxpath: str, xmlpath: str, future: concurrent.futures.Future) -> None:
    try:
        result_str = future.result()
    except Exception as e:
        log.error("test_vm_convert_complete exception: %s", ''.join(str(e).splitlines()))
        result_str = "ERROR"
    GLib.idle_add(test_vm_convert_complete_next, result_str, vmxpath, xmlpath)


def test_vm_convert(name: str, vmxpath: str, xmlpath: str) -> str:
    args: list = ["demo_test_convert.sh", name, vmxpath, xmlpath]
    mappings: list = external_get_mappings()
    if (mappings):
        args.extend(mappings)
    mappings = networks_get_mappings()
    if (mappings):
        args.extend(mappings)
    log.debug("%s", args)
    result_str: str = runcmd(args, True)
    result_str = result_str.strip()
    log.info("test_vm_convert: %s", result_str)
    return result_str


def test_vm_convert_progress(vmxpath: str, xmlpath: str) -> bool:
    global test_executors
    log.debug("test_vm_convert_progress timer triggered")
    GLib.idle_add(convert_progress_idle, vmxpath, xmlpath, test_tree_store, test_executors, test_vm_convert_progress)
    return True


def test_vm(name: str, vmxpath: str, ds_tgt: str):
    global test_executors
    if (tree_store_search(test_tree_store, vmxpath, 3)):
        log.warning("test_vm: already testing %s", vmxpath)
        return
    xmlpath: str = get_xmlpath_from_vmx(vmxpath, ds_tgt)
    log.info("test_vm name:%s vmxpath:%s xmlpath:%s", name, vmxpath, xmlpath)
    test_tree_store.append(None, [name, "Inspecting...", "", vmxpath, xmlpath, 0, 0])

    executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    future: concurrent.futures.Future = executor.submit(test_vm_convert, name, vmxpath, xmlpath)
    timer = GLib.timeout_add(pulse_timer, test_vm_convert_progress, vmxpath, xmlpath)
    test_executors[vmxpath] = {"executor": executor, "timer": timer}
    future.add_done_callback(functools.partial(test_vm_convert_complete, vmxpath, xmlpath))


def test_arrow_clicked(_b: Gtk.Button) -> None:
    log.debug("test_arrow_clicked")
    selection: Gtk.TreeSelection = src_tree_view.get_selection()
    (t, rows) = (selection.get_selected_rows())
    for p in rows:
        it: Gtk.TreeIter = t.get_iter(p)
        child_it: Gtk.TreeIter = t.iter_children(it)
        if not (child_it):
            test_vm(t[it][0], t[it][3], test_datastore)
        while (child_it):
            cp: Gtk.TreePath = t.get_path(child_it)
            if not (cp in rows):
                test_vm(t[cp][0], t[cp][3], test_datastore)
            child_it = t.iter_next(child_it)


def test_arrow_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Test!")
    b.connect("clicked", test_arrow_clicked)
    return b


def migrate_vm_complete_end(result_str: str, vmxpath: str, _xmlpath: str) -> bool:
    global migrate_executors
    if vmxpath in migrate_executors:
        migrate_executors[vmxpath]["executor"].shutdown(wait=False)
        if (migrate_executors[vmxpath]["timer"] >= 0):
            GLib.source_remove(migrate_executors[vmxpath]["timer"])
            migrate_executors[vmxpath]["timer"] = -1
        del migrate_executors[vmxpath]

    row: Gtk.TreeModelRow = tree_store_search(tgt_tree_store, vmxpath, 3)
    if not (row):
        log.warning("migrate_vm_complete_end: %s: not found in tgt_tree_store", vmxpath)
        return False
    row[2] = result_str
    if (result_str == success_str):
        row[1] = migrate_ok_str
        row[5] = 100
        row[6] = -1
    else:
        row[5] = 0
        row[6] = -1
    parent_row: Gtk.TreeModelRow = row.parent
    if not (parent_row):
        log.error("migrate_vm_complete_end: row has no parent: %s", vmxpath)
        return False
    avail_str: str = get_folder_avail_str(parent_row[3])
    parent_row[1] = avail_str
    return False


def migrate_vm_complete(vmxpath: str, xmlpath: str, future: concurrent.futures.Future) -> None:
    try:
        result_str = future.result()
    except Exception as e:
        log.error("migrate_vm_complete exception: %s", ''.join(str(e).splitlines()))
        result_str = "ERROR"
    GLib.idle_add(migrate_vm_complete_end, result_str, vmxpath, xmlpath)


def migrate_vm_convert(name: str, vmxpath: str, xmlpath: str) -> str:
    args: list = ["demo_migrate.sh", name, vmxpath, xmlpath]
    external_mappings: list = external_get_mappings()
    if (external_mappings):
        args.extend(external_mappings)
    network_mappings: list = networks_get_mappings()
    if (network_mappings):
        args.extend(network_mappings)

    log.debug("%s", args)
    result_str: str = runcmd(args, True)
    result_str = result_str.strip()
    log.info("migrate_vm_convert: %s", result_str)
    return result_str


def migrate_vm_convert_progress(vmxpath: str, xmlpath: str) -> bool:
    global migrate_executors
    log.debug("migrate_vm_convert_progress timer triggered")
    GLib.idle_add(convert_progress_idle, vmxpath, xmlpath, tgt_tree_store,
                  migrate_executors, migrate_vm_convert_progress)
    return True


def migrate_vm(name: str, vmxpath: str, tgt_ds: str):
    tgt_row: Gtk.TreeModelRow = tree_store_search(tgt_tree_store, tgt_ds, 3)
    if not (tgt_row):
        log.warning("migrate_vm: no datastore %s found", tgt_ds)
        return
    if (tree_store_search_children(tgt_tree_store, tgt_row, vmxpath, 3)):
        log.warning("migrate_vm: already migrating %s (%s)", name, vmxpath)
        return
    xmlpath = get_xmlpath_from_vmx(vmxpath, tgt_ds)
    log.info("migrate_vm name:%s vmxpath:%s xmlpath:%s", name, vmxpath, xmlpath)
    tgt_tree_store.append(tgt_row.iter, [name, "Starting...", "", vmxpath, xmlpath, 0, 0])

    executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    future: concurrent.futures.Future = executor.submit(migrate_vm_convert, name, vmxpath, xmlpath)
    timer = GLib.timeout_add(pulse_timer, migrate_vm_convert_progress, vmxpath, xmlpath)
    migrate_executors[vmxpath] = {"executor": executor, "timer": timer}
    future.add_done_callback(functools.partial(migrate_vm_complete, vmxpath, xmlpath))


def tgt_arrow_clicked(_b: Gtk.Button) -> None:
    log.debug("tgt_arrow_clicked")
    selection: Gtk.TreeSelection = test_tree_view.get_selection()
    (t, rows) = (selection.get_selected_rows())
    if not (rows):
        selection.select_all()
    (t, rows) = (selection.get_selected_rows())

    for p in rows:
        i: Gtk.TreeIter = t.get_iter(p)
        row: Gtk.TreeModelRow = t[i]
        if (row[1] != test_ok_str or row[2] != success_str):
            # test is not completed successfully for this VM, cannot migrate
            log.warning("tgt_arrow_clicked: %s not testbooted successfully yet", row[3])
            continue
        sr: Gtk.TreeModelRow = tree_store_search(src_tree_store, row[3], 3)
        if not (sr):
            log.warning("tgt_arrow_clicked: %s not in src_tree_store", row[3])
            continue
        if not (sr[4]):
            log.warning("tgt_arrow_clicked: no target datastore chosen for %s", row[3])
            continue
        migrate_vm(sr[0], sr[3], sr[4])


def tgt_arrow_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Conversion!")
    b.connect("clicked", tgt_arrow_clicked)
    return b


class MainWindow(Gtk.Window):
    def __init__(self):
        global vm_find_button
        global src_tree_store, src_tree_view, external_button, networks_button
        global test_arrow
        global test_tree_store, test_tree_view, test_cancel_button
        global tgt_arrow
        global tgt_tree_store, tgt_tree_view, restart_button

        super().__init__(title="Convert to KVM!")
        self.set_border_width(border)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)

        # LAYOUT TITLE
        layout_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout.pack_start(layout_title, False, False, 0)

        header_suse = header_suse_init()
        layout_title.pack_start(header_suse, False, False, 0)
        header_title = header_title_init()
        layout_title.pack_start(header_title, True, True, 0)
        header_kvm = header_kvm_init()
        layout_title.pack_start(header_kvm, False, False, 0)

        # LAYOUT FIND (Entry, Find)
        layout_find = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout.pack_start(layout_find, False, False, 0)
        layout_find_int = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout_find.pack_start(layout_find_int, True, False, 0)

        vm_entry = vm_entry_init()
        layout_find_int.pack_start(vm_entry, False, False, 0)
        vm_find_button = vm_find_button_init()
        layout_find_int.pack_start(vm_find_button, False, False, 0)

        # LAYOUT DS (Source Datastore, Layout Test, Target Datastore)
        layout_ds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_v)
        layout.pack_start(layout_ds, True, True, 0)

        layout_src = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_src, True, True, 0)

        layout_test_arrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_test_arrow.set_margin_top(spacing_v)
        layout_ds.pack_start(layout_test_arrow, True, False, 0)

        test_arrow = arrow_init()
        layout_test_arrow.pack_start(test_arrow, False, False, 0)

        label_src = ds_label_init("Source Datastores")
        layout_src.pack_start(label_src, False, False, 0)
        src_tree_store = tree_store_init()
        src_tree_view = tree_view_init(src_tree_store, layout_src,
                                       ["Name", "Size", "Mapping"], [192, 48, 192], [0, 0, 0])

        layout_maps = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_v)
        layout_src.pack_start(layout_maps, False, False, 0)
        external_button = external_button_init()
        layout_maps.pack_start(external_button, True, True, 0)
        networks_button = networks_button_init()
        layout_maps.pack_start(networks_button, True, True, 0)

        # LAYOUT TEST
        layout_test = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_test, True, True, 0)

        layout_tgt_arrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_tgt_arrow.set_margin_top(spacing_v)
        layout_ds.pack_start(layout_tgt_arrow, True, False, 0)
        tgt_arrow = arrow_init()
        layout_tgt_arrow.pack_start(tgt_arrow, False, False, 0)

        label_test = ds_label_init("Boot Test")
        layout_test.pack_start(label_test, False, False, 0)

        test_tree_store = tree_store_init()
        test_tree_view = tree_view_init(test_tree_store, layout_test,
                                        ["VM Name", "State", "Result"], [192, 128, 112], [0, 2, 0])

        test_cancel_button = test_cancel_button_init()
        layout_test.pack_start(test_cancel_button, False, False, 0)

        # LAYOUT DATASTORES (cont)
        layout_tgt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_tgt, True, True, 0)

        label_tgt = ds_label_init("Target Datastores")
        layout_tgt.pack_start(label_tgt, False, False, 0)

        tgt_tree_store = tree_store_init()
        tgt_tree_view = tree_view_init(tgt_tree_store, layout_tgt,
                                       ["Name", "State", "Result"], [192, 128, 112], [0, 2, 0])

        restart_button = restart_button_init()
        layout_tgt.pack_start(restart_button, False, False, 0)

        self.add(layout)
        self.set_default_size(1920, 1080)
        #self.set_resizable(False)


def external_tree_view_src_activated(_view: Gtk.TreeView, p: Gtk.TreePath, _c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = external_tree_store
    ds_chooser = Gtk.FileChooserDialog(title="Select Source Datastore")
    ds_chooser.set_create_folders(False)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        it: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[it][1] = ds
        t[it][3] = f
    ds_chooser.destroy()


def networks_tree_view_src_activated(_view: Gtk.TreeView, _p: Gtk.TreePath, _c: Gtk.TreeViewColumn):
    pass


def external_tree_view_tgt_activated(_view: Gtk.TreeView, p: Gtk.TreePath, _c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = external_tree_store
    ds_chooser = Gtk.FileChooserDialog(title="Select or Create target datastore folder")
    ds_chooser.set_create_folders(True)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        if not (tree_store_search(tgt_tree_store, f, 3)):
            tgt_tree_store_add(tgt_tree_store, f)
        it: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[it][2] = ds
        t[it][4] = f
    ds_chooser.destroy()


def networks_tree_view_tgt_activated(_view: Gtk.TreeView, _p: Gtk.TreePath, _c: Gtk.TreeViewColumn):
    pass


def external_rescan(_) -> None:
    t: Gtk.TreeStore = external_tree_store
    for row in src_tree_store:
        args: list = ["datastore_find_external_disks.sh", row[3]]
        lines: list = runcmd(args, True).strip().splitlines()
        log.debug("external_rescan: %s: lines: %s", row[3], lines)
        for line in lines:
            # XXX we assume that the datastore is /vmfs/volumes/xxxxxxxx-xxxxxxxx/
            comps: list = os.path.dirname(line).split(os.sep)
            log.debug("external_rescan: comps=%s", comps)
            if (len(comps) > 3 and comps[1] == "vmfs" and comps[2] == "volumes"):
                ds = os.sep + os.path.join(comps[1], comps[2], comps[3])
            else:
                ds = os.path.dirname(os.path.dirname(line))

            if (tree_store_search(t, ds, 0)):
                log.info("external_rescan: %s already in external_tree_store", ds)
            else:
                log.info("external_rescan: appending datastore %s", ds)
                _: Gtk.TreeIter = t.append(None, [ds, "", "", "", "", 0, -1])


def networks_rescan(_) -> None:
    t: Gtk.TreeStore = networks_tree_store
    for row in src_tree_store:
        args: list = ["datastore_find_networks.sh", row[3]]
        lines: list = runcmd(args, True).strip().splitlines()
        log.debug("networks_rescan: %s: lines: %s", row[3], lines)
        for line in lines:
            log.debug("networks_rescan: network=%s", line)
            if not (line.startswith("type:") or line.startswith("name:")):
                continue
            if (tree_store_search(t, line, 0)):
                log.info("networks_rescan: %s already in networks_tree_store", line)
            else:
                log.info("networks_rescan: appending network %s", line)
                _: Gtk.TreeIter = t.append(None, [line, "", "", "", "", 0, -1])


def external_get_mappings() -> list:
    t: Gtk.TreeStore = external_tree_store
    args: list = []
    for row in t:
        ref = row[0]
        ds_src = row[3]
        ds_tgt = row[4]
        if (ds_tgt != "" or ds_src != ""):
            args.extend(["-d", f"{ref},{ds_src}={ds_tgt}"])
    log.info("external_get_mappings: %s", args)
    return args


def networks_get_mappings() -> list:
    t: Gtk.TreeStore = networks_tree_store
    args: list = []
    for row in t:
        net_src = row[0]
        net_tgt = row[1]
        if (net_tgt != ""):
            args.extend(["-n", f"{net_src}={net_tgt}"])
    log.info("networks_get_mappings: %s", args)
    return args


def external_button_clicked(_w: Gtk.Widget):
    global external_window
    log.debug("external_button_clicked")

    external_window.popup()
    external_window.show_all()
    external_window.set_size_request(800, 256)


def external_button_init() -> Gtk.MenuButton:
    global external_window
    b: Gtk.MenuButton = Gtk.MenuButton(label="External Disks", popover=external_window)
    b.connect("clicked", external_button_clicked)
    return b


def external_window_hide(_w: Gtk.Widget, _data) -> bool:
    global external_window
    external_window.hide()
    return True


def external_window_init() -> Gtk.Popover:
    global external_tree_store, external_tree_view
    pop = Gtk.Popover()
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v / 2)

    # LAYOUT TABLE
    layout_table = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    layout.pack_start(layout_table, True, True, 0)
    external_tree_store = tree_store_init()
    external_tree_view = tree_view_init(external_tree_store, layout_table,
                                        ["Volume", "Source DS", "Target DS"], [336, 192, 192], [0, 0, 0])
    pop.add(layout)
    pop.set_position(Gtk.PositionType.BOTTOM)
    return pop


def networks_button_clicked(_w: Gtk.Widget):
    global networks_window
    log.debug("networks_button_clicked")
    networks_window.popup()
    networks_window.show_all()
    networks_window.set_size_request(800, 256)


def networks_button_init() -> Gtk.MenuButton:
    global networks_window
    b: Gtk.MenuButton = Gtk.MenuButton(label="Networks", popover=networks_window)
    b.connect("clicked", networks_button_clicked)
    return b


def networks_window_hide(_w: Gtk.Widget, _data) -> bool:
    global networks_window
    networks_window.hide()
    return True


def networks_window_init() -> Gtk.Popover:
    global networks_tree_store, networks_tree_view
    pop = Gtk.Popover()
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v / 2)

    # LAYOUT TABLE
    layout_table = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    layout.pack_start(layout_table, True, True, 0)
    networks_tree_store = tree_store_init()
    networks_tree_view = tree_view_init(networks_tree_store, layout_table,
                                        ["Source Network", "Target Network"], [256, 256], [0, 1])
    pop.add(layout)
    pop.set_position(Gtk.PositionType.BOTTOM)
    return pop


def get_options() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog='demo.py',
        description="DEMO for the VMX->XML mass test and conversion.",
        usage="%(prog)s [options]\n"
    )

    general = parser.add_argument_group('GENERAL OPTIONS', 'verbosity control, version display')
    general.add_argument('-v', '--verbose', action='count', default=0, help='can be specified up to 2 times')
    general.add_argument('-q', '--quiet', action='count', default=0, help='can be specified up to 2 times')
    general.add_argument('-V', '--version', action='version', version=program_version)

    args: argparse.Namespace = parser.parse_args()
    if (args.verbose and args.quiet):
        log.critical("cannot specify both --verbose and --quiet at the same time.")
        sys.exit(1)
    # initialize logging module
    log_init(args.verbose, args.quiet)


abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

get_options()

external_window = external_window_init()
networks_window = networks_window_init()

w = MainWindow()
if (log.level <= logging.DEBUG):
    w.set_interactive_debugging(True)
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
