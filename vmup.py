#!/usr/bin/env python3

import argparse
import os
import sys

from vmup import builder
from vmup import disk as disk_helper

parser = argparse.ArgumentParser()

parser.add_argument("name", help="the name (and hostname) of the VM")

img_group = parser.add_argument_group("image")
img_group.add_argument("--image-dir",
                       help=("the directory in which to store the images"
                             "(defaults: /var/lib/libvirt/images)"),
                       default="/var/lib/libvirt/images")
img_group.add_argument("--base-image", metavar="PATH_OR_ALIAS",
                       help=("base image to create the main disk from"
                             "(should be a full path or a name, such as "
                             "'fedora', 'fedora-atomic', or 'fedora-23', "
                             "default: fedora"),
                       default='fedora')
img_group.add_argument('--always-fetch',
                       help=("always check the internet for the latest image "
                             "version (default: False)"),
                       action='store_true', default=False)

size_group = parser.add_argument_group("VM size")
# TODO: unify the unit suffix forms (e.g. G vs GiB)
# TODO: support adding multiple disks
size_group.add_argument("--size",
                        help="size of the main disk (default: 20 GiB)",
                        default="20G")
size_group.add_argument("--memory",
                        help=("amount of memory to give the VM"
                              "(default: 3 GiB)"), default="3 GiB")
size_group.add_argument("--cpus",
                        help="number of CPUs to give the VM (default: 2)",
                        default="2", type=int)

dev_group = parser.add_argument_group("devices")
dev_group.add_argument('--net', metavar="TYPE[:arg1=v1,arg2=v2,...]",
                       help=("Configure the type of networking (default or "
                             "OVS).  Arguments such as 'ip=a.b.c.d' may be "
                             "specified to control networking setup."),
                       default="default")

auth_group = parser.add_argument_group("auth")
auth_group.add_argument("--password", help="password for the default user",
                        default=None)
auth_group.add_argument("--ssh-key", metavar="PATH",
                        help=("add an SSH key from a path to the default user "
                              "(default: ~/.ssh/id_rsa.pub)"),
                        action="append",
                        default=[os.path.expanduser("~/.ssh/id_rsa.pub")])
auth_group.add_argument("--user", metavar="NAME",
                        help="set up a custom user instead of the default",
                        default=None)

cmd_group = parser.add_argument_group("files and commands")
cmd_group.add_argument("--share", metavar="HOSTPATH:VMPATH",
                       help="share a directory from the host to the VM",
                       action="append", default=[])
cmd_group.add_argument("--add-file", metavar="SOURCE:DEST[:PERM]",
                       help=("inject a file at the specified path"
                             "(optionally with the given octal permissions)."
                             "If 'SYM:' is prepended, this will create symlink"
                             "inside the VM instead.  If 'DEST' is 'RUN', "
                             "this will insert the file in /tmp and then run "
                             "it with appropriate permissions"),
                       action="append", default=[])
cmd_group.add_argument("--run-cmd", metavar="CMD",
                       help=("run a command after boot"), action="append",
                       default=[])

misc_group = parser.add_argument_group("misc")
misc_group.add_argument("--conn", metavar="URI",
                        help="the libvirt connection to use",
                        default="qemu:///system")
misc_group.add_argument("--new-ci-data",
                        help="overwrite existing cloud-init data",
                        action="store_true", default=False)

raw_args = sys.argv[1:]
dotfile_path = os.path.join(os.getcwd(), '.vmup')
if os.path.exists(dotfile_path):
    with open(dotfile_path) as dotfile:
        raw_args.extend(dotfile.read().split())

args = parser.parse_args(raw_args)

# begin configuration of the VM
vm = builder.VM(args.hostname, image_dir=args.image_dir)

# set the sizes
vm.memory = args.memory
vm.cpus = args.cpus

# fetch the base image
_, backing_file = disk_helper.fetch_image(args.base_image, args.image_dir,
                                          not args.always_fetch)

# provision the disk
vm.provision_disk('main', args.size, backing_file)

# set up 9p shared images
for arg in (arg.split(':') for arg in args.share):
    vm.share_directory(arg[0], arg[1])

# inject files
for arg in (arg.split(':') for arg in args.add_file):
    permissions = None
    if len(arg) > 2 and arg[0] == 'SYM':
        if len(arg) > 3:
            permissions = arg[3]

        permissions = arg[3]
        vm.add_symlink(arg[1], arg[2], permissions=permissions)
    else:
        if len(arg) > 2:
            permissions = arg[2]

        dest = arg[1]
        if arg[1] == 'RUN':
            dest = os.path.join('/tmp', os.path.basename(arg[1]))
            if permissions is None:
                permissions = '0500'

        with open(arg[0], 'rb') as src:
            vm.inject_file(dest, content=src.read(), permissions=permissions)

        if arg[1] == 'RUN':
            vm.run_command(dest)
            vm.run_command(['rm', dest])

# run commands
for cmd in args.run_cmd:
    vm.run_command(cmd)

# load authorized SSH keys
authorized_keys = [open(f).read() for f in args.ssh_key]

# decide on groups
if args.base_image is None or 'fedora' in args.base_image.lower():
    groups = ['wheel', 'adm', 'systemd-journal']
else:
    groups = ['wheel']

# configure user
vm.configure_user(args.user, args.password, groups, authorized_keys)

# set up the networking
net_parts = args.net.split(':')
net_type = net_parts[0]
net_args = {v[0]: v[1] for v in
            (kv.split('=') for kv in net_parts[1].split(','))}
vm.configure_networking(net_type, **net_args)

# write out any remaining data
vm.finalize(recreate_ci=args.new_ci_data)

# define the VM and launch it
vm.launch(conn_uri=args.conn, redefine=args.new_ci_data)
