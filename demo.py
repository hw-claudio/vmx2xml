#! /usr/bin/env python3
#
# Copyright (c) 2024 SUSE LLC
# Written by Claudio Fontana <claudio.fontana@suse.com>
#
# Just a demo for the VMWare to KVM conversion
#

import os
import glob
import gi
import re

from vmx2xml.log import *
from vmx2xml.runcmd import *

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

debug: bool = True
border: int = 48
spacing_min: int = 4
spacing_v: int = 48
spacing_h: int = 48

header_suse: Gtk.Image; header_title: Gtk.Label; header_kvm: Gtk.Image
vm_entry: Gtk.Entry; vm_find: Gtk.Button; button_reset: Gtk.Button;

tree_store_src: Gtk.TreeStore; tree_view_src: Gtk.TreeView; button_src: Gtk.Button
tree_store_test: Gtk.TreeStore; tree_view_test: Gtk.TreeView; button_test: Gtk.Button
tree_store_tgt: Gtk.TreeStore; tree_view_tgt: Gtk.TreeView; button_tgt: Gtk.Button


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


def arrow_init() -> Gtk.Frame:
    f: Gtk.Frame = Gtk.Frame()
    f.set_shadow_type(0)
    i: Gtk.Image = Gtk.Image.new_from_file("art/arrow_dark.png")
    f.add(i)
    return f


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
            log.info("%s already present in the tree", s)
            return row
    log.info("%s not present in the tree", s)
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


def button_src_clicked(widget: Gtk.Widget):
    print("clicked.")


def button_src_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Test!")
    b.connect("clicked", button_src_clicked)
    return b


def button_test_clicked(widget: Gtk.Widget):
    print("clicked.")


def button_test_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Start Conversion!")
    b.connect("clicked", button_test_clicked)
    return b


def button_tgt_clicked(widget: Gtk.Widget):
    tree_store_src.clear()
    tree_store_tgt.clear()
    tree_store_test.clear()


def button_tgt_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button(label="Reset")
    b.connect("clicked", button_tgt_clicked)
    return b


class MainWindow(Gtk.Window):
    def __init__(self):
        global header_suse, header_title, header_kvm
        global vm_entry, vm_find, button_reset
        global tree_store_src, tree_view_src, button_src
        global tree_store_test, tree_view_test, button_test
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

        layout_arrow_src = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_arrow_src.set_margin_top(spacing_v * 2)
        layout_ds.pack_start(layout_arrow_src, True, False, 0)
        arrow_src = arrow_init()
        layout_arrow_src.pack_start(arrow_src, False, False, 0)

        label_src = ds_label_init("Source Datastores")
        layout_src.pack_start(label_src, False, False, 0)
        tree_store_src = tree_store_init()
        tree_view_src = tree_view_init(tree_store_src, layout_src, "Name", "Size", "Mapping")

        # DISABLE BUTTONS, TRY ARROWS
        # layout_button_src = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        # layout_src.pack_start(layout_button_src, False, False, 0)
        # button_src = button_src_init()
        # layout_button_src.pack_start(button_src, True, False, 0)

        # LAYOUT TEST
        layout_test = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_test, True, True, 0)

        layout_arrow_test = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_arrow_test.set_margin_top(spacing_v * 2)
        layout_ds.pack_start(layout_arrow_test, True, False, 0)
        arrow_test = arrow_init()
        layout_arrow_test.pack_start(arrow_test, False, False, 0)

        label_test = ds_label_init("Boot Test")
        layout_test.pack_start(label_test, False, False, 0)

        tree_store_test = tree_store_init()
        tree_view_test = tree_view_init(tree_store_test, layout_test, "VM Name", "%", "Test Result")

        # layout_button_test = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        # layout_test.pack_start(layout_button_test, False, False, 0)
        # button_test = button_test_init()
        # layout_button_test.pack_start(button_test, True, False, 0)

        # LAYOUT DATASTORES (cont)
        layout_tgt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_tgt, True, True, 0)

        label_tgt = ds_label_init("Target Datastores")
        layout_tgt.pack_start(label_tgt, False, False, 0)

        tree_store_tgt = tree_store_init()
        tree_view_tgt = tree_view_init(tree_store_tgt, layout_tgt, "Name", "Avail", "%")

        # layout_button_tgt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        # layout_tgt.pack_start(layout_button_tgt, False, False, 0)
        # button_tgt = button_tgt_init()
        # layout_button_tgt.pack_start(button_tgt, True, False, 0)

        layout_button_reset = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        layout.pack_start(layout_button_reset, False, False, 0)
        button_reset = button_reset_init()
        layout_button_reset.pack_start(button_reset, True, False, 0)

        self.add(layout)
        self.set_default_size(1024, 768)


abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

log_init(1, 0)

w = MainWindow()
w.set_interactive_debugging(debug)
#w.fullscreen()

w.connect("destroy", Gtk.main_quit)
w.show_all()

Gtk.main()
