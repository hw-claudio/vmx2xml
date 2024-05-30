#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Just a demo for the VMWare to KVM conversion
#
# requires Python package psutil (zypper install python311-psutil)
#

import os
import sys
import glob
import gi
import re
import argparse
import concurrent.futures
import psutil

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

# MAIN WINDOW
header_suse: Gtk.Image; header_title: Gtk.Label; header_kvm: Gtk.Image
vm_entry: Gtk.Entry; vm_find: Gtk.Button;
button_restart: Gtk.Button;
tree_store_src: Gtk.TreeStore; tree_view_src: Gtk.TreeView; button_external: Gtk.Button; arrow_test: Gtk.Button
tree_store_test: Gtk.TreeStore; tree_view_test: Gtk.TreeView; button_cancel_test: Gtk.Button; arrow_conv: Gtk.Button
tree_store_tgt: Gtk.TreeStore; tree_view_tgt: Gtk.TreeView;
w: Gtk.Window;

# EXTERNAL WINDOW
external_window: Gtk.Window
tree_store_external: Gtk.TreeStore; tree_view_external: Gtk.TreeView;
button_external_close: Gtk.Button
button_external_rescan: Gtk.Button


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


def tree_store_search(t: Gtk.TreeStore, s: str, i: int) -> Gtk.TreeModelRow:
    for row in t:
        if (s == row[i]):
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
        if (tree_store_search(t, root, 3)):
            continue
        vms: list = []; i: int = 0
        for this in dirs:
            names: list = glob.glob(os.path.join(root, this, "*.vmx"))
            count = len(names)
            for i in range(0, count):
                vms.append({"name": this, "path": names[i]})
        if (len(vms) >= 1):
            tree_store_src_add(t, root, vms)


def tree_view_src_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = tree_store_src
    ds_chooser = Gtk.FileChooserDialog(title="Select or Create target datastore folder")
    ds_chooser.set_create_folders(True)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        if not (tree_store_search(tree_store_tgt, f, 3)):
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
        return tree_view_src_activated(view, p, c)
    elif (view == tree_view_external and (c == view.get_column(0) or c == view.get_column(1))):
        return tree_view_external_src_activated(view, p, c)
    elif (view == tree_view_external):
        return tree_view_external_tgt_activated(view, p, c)


def tree_view_init(s: Gtk.TreeStore, layout: Gtk.Layout, columns: list, csizes: list) -> Gtk.TreeView:
    view: Gtk.TreeView = Gtk.TreeView(model=s)
    renderer: Gtk.CellRendererText = Gtk.CellRendererText()
    for i in range(len(columns)):
        c: Gtk.TreeViewColumn = Gtk.TreeViewColumn(columns[i], renderer, text=i)
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


def button_restart_clicked(widget: Gtk.Widget):
    tree_store_src.clear()
    tree_store_tgt.clear()
    tree_store_test.clear()


def button_restart_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Restart")
    b.connect("clicked", button_restart_clicked)
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


def button_cancel_test_clicked(widget: Gtk.Widget):
    global executors
    for vmxpath in executors:
        executors[vmxpath].shutdown(wait=False)
    kill_child_processes(os.getpid())
    executors = {}
    tree_store_test.clear()


def button_cancel_test_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Cancel Test")
    b.connect("clicked", button_cancel_test_clicked)
    return b


def ds_label_init(text: str) -> Gtk.Label:
    l: Gtk.Label = Gtk.Label(label=text)
    c = l.get_style_context()
    c.add_class("ds_label");
    return l


def test_vm_complete_update(result_str: str, vmxpath: str):
    row: Gtk.TreeModelRow = tree_store_search(tree_store_test, vmxpath, 3)
    if not (row):
        log.info("test_vm_complete_update: %s: not found in tree_store_test", vmxpath)
        return
    row[1] = "100 %"
    row[2] = result_str
    if vmxpath in executors:
        executors[vmxpath].shutdown(wait=False)
        del executors[vmxpath]


def test_vm_complete(future) -> None:
    try:
        (result_str, vmxpath) = future.result()
    except Exception as e:
        log.error("test_vm exception: %s", ''.join(str(e).splitlines()))
        return
    GLib.idle_add(test_vm_complete_update, result_str, vmxpath)


def testboot_xml(name: str, vmxpath: str, ds: str) -> tuple:
    args: list = ["demo_testboot_xml.sh", name, vmxpath, ds]
    mappings: list = external_get_mappings()
    if (mappings):
        args.extend(mappings)
    result_str: str = runcmd(args, True)
    result_str = result_str.strip()
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
        global vm_entry, vm_find, button_restart
        global tree_store_src, tree_view_src, button_external, arrow_test
        global tree_store_test, tree_view_test, button_cancel_test, arrow_conv
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
        tree_view_src = tree_view_init(tree_store_src, layout_src, ["Name", "Size", "Mapping"], [256, 48, 92])

        button_external = button_external_init()
        layout_src.pack_start(button_external, False, False, 0)

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
        tree_view_test = tree_view_init(tree_store_test, layout_test, ["VM Name", "%", "Test State"], [256, 48, 92])

        button_cancel_test = button_cancel_test_init()
        layout_test.pack_start(button_cancel_test, False, False, 0)

        # LAYOUT DATASTORES (cont)
        layout_tgt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_tgt, True, True, 0)

        label_tgt = ds_label_init("Target Datastores")
        layout_tgt.pack_start(label_tgt, False, False, 0)

        tree_store_tgt = tree_store_init()
        tree_view_tgt = tree_view_init(tree_store_tgt, layout_tgt, ["Name", "Avail", "%"], [256, 48, 92])

        layout_button_restart = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        layout.pack_start(layout_button_restart, False, False, 0)
        button_restart = button_restart_init()
        layout_button_restart.pack_start(button_restart, True, False, 0)

        self.add(layout)
        self.set_default_size(1920, 1080)
        #self.set_resizable(False)


def tree_view_external_src_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = tree_store_external
    ds_chooser = Gtk.FileChooserDialog(title="Select Source Datastore")
    ds_chooser.set_create_folders(False)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        iter: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[iter][1] = ds
        t[iter][3] = f
    ds_chooser.destroy()


def tree_view_external_tgt_activated(view: Gtk.TreeView, p: Gtk.TreePath, c: Gtk.TreeViewColumn):
    t: Gtk.TreeStore = tree_store_external
    ds_chooser = Gtk.FileChooserDialog(title="Select or Create target datastore folder")
    ds_chooser.set_create_folders(True)
    ds_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    ds_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = ds_chooser.run()

    if (response == Gtk.ResponseType.OK):
        f: str = ds_chooser.get_filename()
        if not (tree_store_search(tree_store_tgt, f, 3)):
            tree_store_tgt_add(tree_store_tgt, f)
        iter: Gtk.TreeIter = t.get_iter(p)
        ds: str = os.path.basename(f)
        t[iter][2] = ds
        t[iter][4] = f
    ds_chooser.destroy()


def external_rescan(unusedp) -> None:
    t: Gtk.TreeStore = tree_store_external
    for row in tree_store_src:
        args: list = [ "datastore_find_external_disks.sh", row[3] ]
        lines: list = runcmd(args, True).strip().splitlines()
        log.debug("external_rescan: %s: lines: %s", row[3], lines)
        for line in lines:
            # XXX we assume that the datastore is two dirs higher than the vmx
            ds: str = os.path.dirname(os.path.dirname(line))
            if (tree_store_search(t, ds, 0)):
                log.info("external_rescan: %s already in tree_store_external", ds)
            else:
                log.info("external_rescan: appending datastore %s", ds)
                iter: Gtk.TreeIter = t.append(None, [ds, "", "", "", ""])


def external_get_mappings() -> list:
    t: Gtk.TreeStore = tree_store_external
    args: list = []
    for row in t:
        ref = row[0]
        ds_src = row[3]
        ds_tgt = row[4]
        args.append(f"-d{ref},{ds_src}={ds_tgt}")
    log.info("external_get_mappings: %s", args)
    return args


def button_external_clicked(widget: Gtk.Widget):
    global w
    external_window.show_all()
    external_window.set_transient_for(w)
    external_window.present()
    external_window.move(0, 480)

    if (len(tree_store_external) < 1):
        external_rescan(None)


def button_external_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="External Disks")
    b.connect("clicked", button_external_clicked)
    return b


def external_title_init() -> Gtk.Label:
    l: Gtk.Label = ds_label_init("External Disks")
    c = l.get_style_context()
    c.add_class("ds_label");
    return l


def external_window_hide(w: Gtk.Widget, data) -> bool:
    global external_window
    external_window.hide()
    return True


def button_external_rescan_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Rescan")
    b.connect("clicked", external_rescan)
    return b


def button_external_close_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Close")
    b.connect("clicked", external_window_hide, "")
    return b


class ExternalWindow(Gtk.Window):
    def __init__(self):
        global tree_store_external, tree_view_external

        super().__init__(title="External Disks Datastore Mappings")
        self.set_border_width(border)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)

        # LAYOUT TITLE
        layout_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout.pack_start(layout_title, False, False, 0)
        external_title = external_title_init()
        layout_title.pack_start(external_title, True, True, 0)

        # LAYOUT TABLE
        layout_table = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout.pack_start(layout_table, True, True, 0)
        tree_store_external = tree_store_init()
        tree_view_external = tree_view_init(tree_store_external, layout_table, ["Disk Path", "Source DS", "Target DS"], [ 256, 192, 192 ])

        # LAYOUT FOOTER
        layout_foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout.pack_start(layout_foot, False, False, 0)
        button_external_close = button_external_close_init()
        layout_foot.pack_start(button_external_close, True, False, 0)
        button_external_rescan = button_external_rescan_init()
        layout_foot.pack_start(button_external_rescan, True, False, 0)
        self.add(layout)
        self.set_default_size(800, 480)


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

external_window = ExternalWindow()
external_window.connect("delete-event", external_window_hide)

w.show_all()
Gtk.main()
