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
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

debug: bool = True
border: int = 48
spacing_min: int = 4
spacing_v: int = 48
spacing_h: int = 48

header_suse: Gtk.Image; header_title: Gtk.Label; header_kvm: Gtk.Image
vm_entry: Gtk.Entry; vm_find: Gtk.Button

tree_store_src: Gtk.TreeStore; tree_view_src: Gtk.TreeView; button_src: Gtk.Button
tree_store_test: Gtk.TreeStore; tree_view_test: Gtk.TreeView; button_test: Gtk.Button
tree_store_tgt: Gtk.TreeStore; tree_view_tgt: Gtk.TreeView; button_tgt: Gtk.Button


def header_suse_init() -> Gtk.Image:
    i: Gtk.Image = Gtk.Image.new_from_file("art/suse-logo-small-h.png")
    return i


def header_title_init() -> Gtk.Label:
    l: Gtk.Label = Gtk.Label("Convert to KVM!")
    c = l.get_style_context()
    c.add_class("title");
    return l


def header_kvm_init() -> Gtk.Image:
    i: Gtk.Image = Gtk.Image.new_from_file("art/kvm-logo.png")
    return i


def vm_entry_init() -> Gtk.Entry:
    e: Gtk.Entry = Gtk.Entry()
    e.set_text("Find VMs to convert  >>>")
    e.set_editable(False)
    e.set_alignment(1)
    return e


def tree_store_init() -> Gtk.TreeStore:
    s: Gtk.TreeStore = Gtk.TreeStore(str, str, str)
    return s


def tree_store_add_walk(t: Gtk.TreeStore, folder: str) -> None:
    for (root, dirs, files) in os.walk(folder, topdown=True):
        vms: int = 0; count: int = 0
        for this in dirs:
            vmxnames: list = glob.glob(os.path.join(root, this, "*.vmx"))
            count = len(vmxnames)
            if (count >= 1):
                vms += count
        if (vms >= 1):
            t.append(None, [root, str(vms) + " VMs", "None"])
            del dirs
            del root
            continue


def tree_view_init(s: Gtk.TreeStore, first:str, second: str, third: str) -> Gtk.TreeView:
    t: Gtk.TreeView = Gtk.TreeView(model=s)
    renderer: Gtk.CellRendererText = Gtk.CellRendererText()
    column: Gtk.TreeViewColumn = Gtk.TreeViewColumn(first, renderer, text=0)
    column.set_expand(True)
    column.set_resizable(True)
    t.append_column(column)
    column = Gtk.TreeViewColumn(second, renderer, text=1)
    column.set_resizable(True)
    t.append_column(column)
    column = Gtk.TreeViewColumn(third, renderer, text=2)
    column.set_resizable(True)
    t.append_column(column)
    return t


def vm_find_clicked(widget: Gtk.Widget):
    vm_chooser = Gtk.FileChooserDialog(title="Select Folder to scan for VMX files")
    vm_chooser.set_create_folders(False)
    vm_chooser.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
    vm_chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,)
    response = vm_chooser.run()

    if (response == Gtk.ResponseType.OK):
        tree_store_add_walk(tree_store_src, vm_chooser.get_filename())
    vm_chooser.destroy()


def vm_find_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button("Find")
    b.connect("clicked", vm_find_clicked)
    return b


def ds_label_init(text: str) -> Gtk.Label:
    l: Gtk.Label = Gtk.Label(text)
    c = l.get_style_context()
    c.add_class("ds_label");
    return l


def button_src_clicked(widget: Gtk.Widget):
    print("clicked.")


def button_src_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button("Start Test!")
    b.connect("clicked", button_src_clicked)
    return b


def button_test_clicked(widget: Gtk.Widget):
    print("clicked.")


def button_test_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button("Start Conversion!")
    b.connect("clicked", button_test_clicked)
    return b


def button_tgt_clicked(widget: Gtk.Widget):
    tree_store_src.clear()
    tree_store_tgt.clear()
    tree_store_test.clear()


def button_tgt_init() -> Gtk.Button:
    b: Gtk.Button = Gtk.Button("Reset")
    b.connect("clicked", button_tgt_clicked)
    return b


class MainWindow(Gtk.Window):
    def __init__(self):
        global header_suse, header_title, header_kvm
        global vm_entry, vm_find
        global tree_store_src, tree_view_src, button_src
        global tree_store_test, tree_view_test, button_test
        global tree_store_tgt, tree_view_tgt, button_tgt

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

        # LAYOUT DS (Source Datastore, Layout Middle, Target Datastore)
        layout_ds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_h)
        layout.pack_start(layout_ds, True, True, 0)

        layout_src = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_src, False, False, 0)
        # invisible, just for the spacing
        align_src = Gtk.Label()
        layout_src.pack_start(align_src, False, False, 0)

        label_src = ds_label_init("Source Datastore")
        layout_src.pack_start(label_src, False, False, 0)
        tree_store_src = tree_store_init()
        tree_view_src = tree_view_init(tree_store_src, "Name", "Size", "Mapping")
        layout_src.pack_start(tree_view_src, True, True, 0)
        button_src = button_src_init()
        layout_src.pack_start(button_src, False, False, 0)

        # LAYOUT MIDDLE (Entry+Find, Test)
        layout_mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_mid, False, False, 0)

        # LAYOUT FIND (Entry, Find)
        layout_find = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        layout_mid.pack_start(layout_find, False, False, 0)

        vm_entry = vm_entry_init()
        layout_find.pack_start(vm_entry, False, False, 0)
        vm_find = vm_find_init()
        layout_find.pack_start(vm_find, False, False, 0)

        label_test = ds_label_init("Boot Test")
        layout_mid.pack_start(label_test, False, False, 0)

        tree_store_test = tree_store_init()
        tree_view_test = tree_view_init(tree_store_test, "VM Name", "Progress", "Test Result")
        layout_mid.pack_start(tree_view_test, True, True, 0)

        button_test = button_test_init()
        layout_mid.pack_start(button_test, False, False, 0)

        # LAYOUT DATASTORES (cont)
        layout_tgt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing_v)
        layout_ds.pack_start(layout_tgt, False, False, 0)
        # invisible, just for the spacing
        align_tgt = Gtk.Label()
        layout_tgt.pack_start(align_tgt, False, False, 0)

        label_tgt = ds_label_init("Target Datastore")
        layout_tgt.pack_start(label_tgt, False, False, 0)

        tree_store_tgt = tree_store_init()
        tree_view_tgt = tree_view_init(tree_store_tgt, "Name", "Size", "Progress")
        layout_tgt.pack_start(tree_view_tgt, True, True, 0)

        button_tgt = button_tgt_init()
        layout_tgt.pack_start(button_tgt, False, False, 0)

        self.add(layout)
        self.set_default_size(800, 600)


abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

w = MainWindow()
w.set_interactive_debugging(debug)
#w.fullscreen()

w.connect("destroy", Gtk.main_quit)
w.show_all()

Gtk.main()
