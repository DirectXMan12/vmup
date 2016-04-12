#!/usr/bin/env python3

import argparse
import logging
import os
import shlex
import sys

import requests

from vmup import builder
from vmup import disk as disk_helper


LOG = logging.getLogger(__name__)

parser = argparse.ArgumentParser()

parser.add_argument("name", help="the name (and hostname) of the VM")

img_group = parser.add_argument_group("image")
img_group.add_argument("--image-dir",
                       help=("the directory in which to store the images"
                             "(defaults: /var/lib/libvirt/images)"),
                       default="POOL:default")
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
                        default="20 GiB")
size_group.add_argument("--memory",
                        help=("amount of memory to give the VM"
                              "(default: 3 GiB)"), default="3 GiB")
size_group.add_argument("--cpus",
                        help="number of CPUs to give the VM (default: 2)",
                        default="2", type=int)

dev_group = parser.add_argument_group("devices")
dev_group.add_argument('--net', metavar="TYPE[:arg1=v1,arg2=v2,...]",
                       help=("Configure the type of networking (default, ovs, "
                             "or none).  Arguments such as 'ip=a.b.c.d' may "
                             "be specified to control networking setup."),
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
cmd_group.add_argument("--add-packages", metavar="PKGS", default=[], nargs='+',
                       help="install the given package in the VM (multiple "
                            "packages may be installed by listing multiple "
                            "space-separated packages)")
cmd_group.add_argument("--add-repo", metavar="REPO_FILE_OR_URL",
                       action="append", default=[],
                       help="add the given YUM repos to the VM")

misc_group = parser.add_argument_group("misc")
misc_group.add_argument("--conn", metavar="URI",
                        help="the libvirt connection to use",
                        default="qemu:///system")
misc_group.add_argument("--new-ci-data",
                        help="overwrite existing cloud-init data",
                        action="store_true", default=False)
misc_group.add_argument("--burn",
                        help="overwrite everything, stopping the existing VM "
                             "(if present) in the process", default=False,
                             action='store_true')
misc_group.add_argument("--halt-existing",
                        help="stop the existing VM if needed",
                        default=False, action='store_true')
misc_group.add_argument("-v", metavar="LEVEL", default='INFO',
                        help="set the logging verbosity (may be debug, info, "
                             "warning, error, or critical, default: info)")

raw_args = []

dotrc_path = os.path.expanduser('~/.vmuprc')
if os.path.exists(dotrc_path):
    with open(dotrc_path) as dotrc:
        raw_args.extend(shlex.split(dotrc.read(), comments=True))

dotfile_path = os.path.join(os.getcwd(), '.vmup')
if os.path.exists(dotfile_path):
    with open(dotfile_path) as dotfile:
        raw_args.extend(shlex.split(dotfile.read(), comments=True))

# TODO: manually expanduser on the raw_args arguments?
raw_args.extend(sys.argv[1:])

args = parser.parse_args(raw_args)

# --burn implies the other overwrite options
if args.burn:
    args.new_ci_data = True
    args.halt_existing = True

args.v = args.v.upper()
if args.v not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
    sys.exit('Invalid verbosity %s' % args.v)

logging.basicConfig(level=getattr(logging, args.v))

LOG.debug('All arguments: %s' % raw_args)

# begin configuration of the VM
vm = builder.VM(args.name, image_dir=args.image_dir,
                conn_uri=args.conn)

if vm.load_existing(halt=args.halt_existing):
    sys.exit("Cowardly refusing to overwrite a running VM.  Try running with "
             "--halt-existing")

# set the sizes
vm.memory = args.memory
vm.cpus = args.cpus

backing_file = vm.fetch_base_image(args.base_image, args.always_fetch)

# provision the disk
vm.provision_disk('main', args.size, backing_file,
                  overwrite=args.burn)

# set up 9p shared images
for arg in (arg.split(':') for arg in args.share):
    writable = False
    mode = None

    if len(arg) > 2:
        mode_args = arg[2].split('-')
        writable = (mode_args[0] == 'rw')
        if len(mode_args) > 1:
            mode = mode_args[1]

    vm.share_directory(os.path.abspath(arg[0]),
                       arg[1], writable=writable, mode=mode)

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
net_args = {}
if len(net_parts) > 1:
    net_args = {v[0]: v[1] for v in
                (kv.split('=') for kv in net_parts[1].split(','))}
vm.configure_networking(net_type, **net_args)

# configure YUM repos
for repo in args.add_repo:
    if repo.startswith('http://') or repo.startswith('https://'):
        req = requests.get(repo)
        repo_file_contents = req.text
    else:
        with open(repo) as repo_file:
            repo_file_contents = repo_file.read()

    vm.use_repo(repo_file_contents)

# NB: sross Fedora seems to have some AVC issues with doing an upgrade
# vm.upgrade_all_packages()
# install packages
for pkg in args.add_packages:
    vm.install_package(*pkg.split('-', 1))

# write out any remaining data
vm.finalize(recreate_ci=args.new_ci_data)

# define the VM and launch it
vm.launch(redefine=args.new_ci_data)
