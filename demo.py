#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Just a demo for the VMWare to KVM conversion
#

import os
import sys
import glob
import gi
import re
import argparse
import concurrent.futures

from vmx2xml.log import *
from vmx2xml.runcmd import *

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

program_version: str = "0.1"
border: int = 48
spacing_min: int = 4
spacing_v: int = 48
spacing_h: int = 48
test_datastore: str = "/vm_testboot"
executors: dict = {}

header_suse: Gtk.Image; header_title: Gtk.Label; header_kvm: Gtk.Image
vm_entry: Gtk.Entry; vm_find: Gtk.Button; button_reset: Gtk.Button;

tree_store_src: Gtk.TreeStore; tree_view_src: Gtk.TreeView; arrow_test: Gtk.Button
tree_store_test: Gtk.TreeStore; tree_view_test: Gtk.TreeView; arrow_conv: Gtk.Button
tree_store_tgt: Gtk.TreeStore; tree_view_tgt: Gtk.TreeView;

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


def arrow_pressed(b: Gtk.Button, e: Gdk.EventButton) -> bool:
    log.info("arrow_pressed! b=%s", b)
    arrow_light = Gtk.Image.new_from_file("art/arrow_light.png")
    b.set_image(arrow_light)
    return False

def arrow_clicked(b: Gtk.Button):
    log.info("arrow_clicked! b=%s", b)
    arrow_dark = Gtk.Image.new_from_file("art/arrow_dark.png")
    b.set_image(arrow_dark)
    if (b == arrow_test):
        arrow_test_clicked(b)
    elif (b == arrow_conv):
        arrow_conv_clicked(b)


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
    c.add_class("title");
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
    s: Gtk.TreeStore = Gtk.TreeStore(str, str, str, str, str)
    return s


def tree_store_search(t: Gtk.TreeStore, s: str) -> Gtk.TreeModelRow:
    for row in t:
        if (s == row[3]):
            log.debug("%s already present in the tree", s)
            return row
    log.debug("%s not present in the tree", s)
    return None


def tree_store_src_add(t: Gtk.TreeStore, root: str, vms: list) -> None:
    size_str: str = get_folder_size_str(root)
    iter: Gtk.TreeIter = t.append(None, [os.path.basename(root), size_str, "None", root, ""])
    for vm in vms:
        size_str = get_folder_size_str(os.path.dirname(vm["path"]))
        t.append(iter, [vm["name"], size_str, "None", vm["path"], ""])


def tree_store_tgt_add(t: Gtk.TreeStore, root: str) -> None:
    avail_str: str = get_folder_avail_str(root)
    t.append(None, [os.path.basename(root), avail_str, "", root, ""])


def tree_store_src_walk(t: Gtk.TreeStore, folder: str) -> None:
    for (root, dirs, files) in os.walk(folder, topdown=True):
        if (tree_store_search(t, root)):
            continue
        vms: list = []; i: int = 0
        for this in dirs:
            names: list = glob.glob(os.path.join(root, this, "*.vmx"))
            count = len(names)
            for i in range(0, count):
                vms.append({"name": this, "path": names[i]})
        if (len(vms) >= 1):
            tree_store_src_add(t, root, vms)


def tree_view_src_row_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = tree_store_src
    ds_chooser = Gtk.FileChooserDialog(title="Select or Create target datastore folder")
    ds_chooser.set_create_folders(True)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        if not (tree_store_search(tree_store_tgt, f)):
            tree_store_tgt_add(tree_store_tgt, f)
        iter: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[iter][2] = ds
        t[iter][4] = f
        child_iter: Gtk.TreeIter = t.iter_children(iter)
        while (child_iter):
            t[child_iter][2] = ds
            t[child_iter][4] = f
            child_iter = t.iter_next(child_iter)
    ds_chooser.destroy()


def tree_view_row_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    if (view == tree_view_src):
        return tree_view_src_row_activated(view, p, c)


def tree_view_init(s: Gtk.TreeStore, layout: Gtk.Layout, first:str, second: str, third: str) -> Gtk.TreeView:
    view: Gtk.TreeView = Gtk.TreeView(model=s)
    renderer: Gtk.CellRendererText = Gtk.CellRendererText()
    column: Gtk.TreeViewColumn = Gtk.TreeViewColumn(first, renderer, text=0)
    column.set_min_width(256)
    column.set_max_width(256)
    column.set_fixed_width(256)
    column.set_resizable(False)
    column.set_sizing(2)
    view.append_column(column)

    column = Gtk.TreeViewColumn(second, renderer, text=1)
    column.set_resizable(False)
    column.set_min_width(48)
    column.set_max_width(48)
    column.set_fixed_width(48)
    column.set_sizing(2)
    view.append_column(column)

    column = Gtk.TreeViewColumn(third, renderer, text=2)
    column.set_resizable(False)
    column.set_min_width(92)
    column.set_max_width(92)
    column.set_fixed_width(92)
    column.set_sizing(2)
    view.append_column(column)

    view.connect("row-activated", tree_view_row_activated)
    selection: Gtk.TreeSelection = view.get_selection()
    selection.set_mode(Gtk.SelectionMode.MULTIPLE)
    tree_view_scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow()
    tree_view_scroll.add(view)
    tree_view_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
    layout.pack_start(tree_view_scroll, True, True, 0)
    return view


def vm_find_clicked(widget: Gtk.Widget):
    vm_chooser = Gtk.FileChooserDialog(title="Select Folder to scan for VMX files")
    vm_chooser.set_create_folders(False)
    vm_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    vm_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = vm_chooser.run()

    if (response == Gtk.ResponseType.OK):
        tree_store_src_walk(tree_store_src, vm_chooser.get_filename())
    vm_chooser.destroy()


def vm_find_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Find")
    b.connect("clicked", vm_find_clicked)
    return b


def button_reset_clicked(widget: Gtk.Widget):
    tree_store_src.clear()
    tree_store_tgt.clear()
    tree_store_test.clear()


def button_reset_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Restart")
    b.connect("clicked", button_reset_clicked)
    return b


def ds_label_init(text: str) -> Gtk.Label:
    l: Gtk.Label = Gtk.Label(label=text)
    c = l.get_style_context()
    c.add_class("ds_label");
    return l


def test_vm_complete_update(result_str: str, vmxpath: str):
    row: Gtk.TreeModelRow = tree_store_search(tree_store_test, vmxpath)
    if not (row):
        log.info("test_vm_complete_update: %s: not found in tree_store_test")
        return
    row[1] = "100 %"
    row[2] = result_str


def test_vm_complete(future) -> None:
    try:
        (result_str, vmxpath) = future.result()
    except Exception as e:
        log.error("test_vm %s exception: %s", e)
        return
    GLib.idle_add(test_vm_complete_update, result_str, vmxpath)


def testboot_xml(name: str, vmxpath: str, ds: str) -> str:
    result_str: str = runcmd(["demo_testboot_xml.sh", name, vmxpath, ds], True)
    log.info("testboot_xml: %s", result_str)
    return (result_str, vmxpath)


def test_vm(name: str, vmxpath: str, ds: str, iter: Gtk.TreeIter):
    global executors
    log.info("test_vm name:%s vmx:%s ds:%s", name, vmxpath, ds)
    tree_store_test.append(None, [name, "0 %", "Starting", vmxpath, ""])
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    executors[vmxpath] = executor
    future = executor.submit(testboot_xml, name, vmxpath, ds)
    future.add_done_callback(test_vm_complete)


def arrow_test_clicked(b: Gtk.Button) -> None:
    log.debug("arrow_test_clicked")
    selection: Gtk.TreeSelection = tree_view_src.get_selection()
    (t, rows) = (selection.get_selected_rows())
    for p in rows:
        iter: Gtk.TreeIter = t.get_iter(p)
        child_iter: Gtk.TreeIter = t.iter_children(iter)
        if not (child_iter):
            test_vm(t[iter][0], t[iter][3], test_datastore, iter)
        while (child_iter):
            cp: Gtk.TreePath = t.get_path(child_iter)
            if not (cp in rows):
                test_vm(t[cp][0], t[cp][3], test_datastore, child_iter)
            child_iter = t.iter_next(child_iter)


def arrow_test_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Test!")
    b.connect("clicked", arrow_test_clicked)
    return b


def arrow_conv_clicked(b: Gtk.Button) -> None:
    log.debug("arrow_conv_clicked")


def arrow_conv_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Conversion!")
    b.connect("clicked", arrow_conv_clicked)
    return b


class MainWindow(Gtk.Window):
    def __init__(self):
        global header_suse, header_title, header_kvm
        global vm_entry, vm_find, button_reset
        global tree_store_src, tree_view_src, arrow_test
        global tree_store_test, tree_view_test, arrow_conv
        global tree_store_tgt, tree_view_tgt

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
        vm_find = vm_find_init()
        layout_find_int.pack_start(vm_find, False, False, 0)

        # LAYOUT DS (Source Datastore, Layout Test, Target Datastore)
        layout_ds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        layout.pack_start(layout_ds, True, True, 0)

        layout_src = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_src, True, True, 0)

        layout_arrow_test = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_arrow_test.set_margin_top(spacing_v * 2)
        layout_ds.pack_start(layout_arrow_test, True, False, 0)

        arrow_test = arrow_init()
        layout_arrow_test.pack_start(arrow_test, False, False, 0)

        label_src = ds_label_init("Source Datastores")
        layout_src.pack_start(label_src, False, False, 0)
        tree_store_src = tree_store_init()
        tree_view_src = tree_view_init(tree_store_src, layout_src, "Name", "Size", "Mapping")

        # LAYOUT TEST
        layout_test = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_test, True, True, 0)

        layout_arrow_conv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_arrow_conv.set_margin_top(spacing_v * 2)
        layout_ds.pack_start(layout_arrow_conv, True, False, 0)
        arrow_conv = arrow_init()
        layout_arrow_conv.pack_start(arrow_conv, False, False, 0)

        label_test = ds_label_init("Boot Test")
        layout_test.pack_start(label_test, False, False, 0)

        tree_store_test = tree_store_init()
        tree_view_test = tree_view_init(tree_store_test, layout_test, "VM Name", "%", "Test State")

        # LAYOUT DATASTORES (cont)
        layout_tgt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_tgt, True, True, 0)

        label_tgt = ds_label_init("Target Datastores")
        layout_tgt.pack_start(label_tgt, False, False, 0)

        tree_store_tgt = tree_store_init()
        tree_view_tgt = tree_view_init(tree_store_tgt, layout_tgt, "Name", "Avail", "%")

        layout_button_reset = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        layout.pack_start(layout_button_reset, False, False, 0)
        button_reset = button_reset_init()
        layout_button_reset.pack_start(button_reset, True, False, 0)

        self.add(layout)
        self.set_default_size(1920, 1080)
        #self.set_resizable(False)


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
    if (args.verbose > 2):
        args.verbose = 2
    if (args.quiet > 2):
        args.quiet = 2
    # initialize logging module
    log_init(args.verbose, args.quiet)


abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

get_options()

w = MainWindow()
if (log.level <= logging.DEBUG):
    w.set_interactive_debugging(True)
#w.fullscreen()
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
