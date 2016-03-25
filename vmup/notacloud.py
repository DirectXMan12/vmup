import copy
import os
import re
import time
import tempfile

import yaml

from vmup import disk as disk_helpers

_HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-]{0,62}(?<!-)$')


def _validate_label(label):
    if not _HOSTNAME_RE.match(label):
        raise ValueError("Invalid hostname segement '{label}'".format(label))


def _validate_hostname(name):
    if len(name) > 253:
        raise ValueError("Hostname has length "
                         "({len}) > 253".format(len=len(name)))

    for label in name.split('.'):
        _validate_label(label)


def get_metadata(name, net=None):
    # TODO: set hostname vs local-hostname?
    _validate_hostname(name)
    instance_id = "{name}-{ts}".format(name=name.replace('.', '-'),
                                       ts=int(time.time()))
    res = ("instance-id: {inst_id}\n"
           "local-hostname: {hostname}").format(inst_id=instance_id,
                                                hostname=name)

    if net is not None:
        res += "\nnetwork-interfaces: |\n" + "\n".join('  ' + l for l in net)

    return res


class UserData(object):
    def __getstate__(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items()
                if not k.startswith('_')}

    def __init__(self):
        self._default_added = False

    def set_passwords(self, passwords=None, expire=None):
        self.chpasswd = {}
        if expire is not None:
            self.chpasswd['expire'] = expire

        if passwords is not None:
            self.chpasswd['list'] = "\n".join('{u}:{p}'.format(u=u, p=p)
                                              for u, p in passwords.items())

    def allow_ssh_password_auth(self, val=True):
        self.ssh_pwauth = val

    def add_default_user(self, password=None, authorized_keys=None):
        self._default_added = True
        if getattr(self, 'users', None) is not None:
            self.users.append('default')

        if password is not None:
            self.password = password

        if authorized_keys is not None:
            self.ssh_authorized_keys = authorized_keys

    def add_user(self, name, **kwargs):
        if getattr(self, 'users', None) is None:
            self.users = []
            if self._default_added:
                self.users.append('default')

        user = {'name': name}

        groups = kwargs.pop('groups', None)
        if groups is not None:
            user['groups'] = ', '.join(groups)

        lock_password = kwargs.pop('lock_password', None)
        if lock_password is not None:
            user['lock-passwd'] = lock_password

        password_hash = kwargs.pop('password_hash', None)
        if password_hash is not None:
            user['passwd'] = password_hash

        create_home = kwargs.pop('create_home', True)
        if not create_home:
            user['no-create-home'] = True

        create_user_group = kwargs.pop('create_user_group', True)
        if not create_user_group:
            user['no-user-group'] = True

        init_logs = kwargs.pop('init_logs', True)
        if not init_logs:
            user['no-log-init'] = True

        for k, v in kwargs.items():
            user[k.replace('_', '-')] = v

        self.users.append(user)

    def add_group(self, name, members=None):
        if getattr(self, 'groups', None) is None:
            self.groups = []

        if members is None:
            self.groups.append(name)
        else:
            self.groups.append({name: members})

    def add_file(self, path, content, **kwargs):
        if getattr(self, 'write_files', None) is None:
            self.write_files = []

        file_info = {'content': content, 'path': path}
        for k, v in kwargs.items():
            file_info[k.replace('_', '-')] = v

        self.write_files.append(file_info)

    def configure_yum_repo(self, name, desc, baseurl, enabled=True, **kwargs):
        if getattr(self, 'yum_repos', None) is None:
            self.yum_repos = {}

        repo_info = {'name': desc, 'baseurl': baseurl, 'enabled': enabled}
        for k, v in kwargs.items():
            repo_info[k.replace('_', '-')] = v

        self.yum_repos[name] = repo_info

    def install_package(self, name, version=None):
        if getattr(self, 'packages', None) is None:
            self.packages = []

        if version is None:
            self.packages.append(name)
        else:
            self.packages.append([name, version])

    def run_upgrade(self, val=True):
        self.package_upgrade = val

    def add_mount(self, *args):
        if getattr(self, 'mounts', None) is None:
            self.mounts = []

        args = [str(arg) for arg in args]

        if len(args) > 6 or len(args) < 0:
            raise ValueError("'{line}' is not a valid fstab line".format(
                line=' '.join(args)))

        self.mounts.append(args)

    def set_mount_defaults(self, *args):
        args = [str(arg) for arg in args]

        if len(args) != 6:
            raise ValueError("'{line}' is not a valid fstab line".format(
                line=' '.join(args)))

        self.mount_default_fields = args

    def configure_swap(self, filename, maxsize, size="auto"):
        self.swap = {'filename': filename, 'size': size, maxsize: maxsize}

    def run_command(self, command, when=None, freq=None, ind=None):
        if when is None:
            if getattr(self, 'runcmd', None) is None:
                self.runcmd = []

            if ind is None:
                ind = len(self.runcmd)

            self.runcmd.insert(ind, command)
        elif when == 'boot':
            if getattr(self, 'bootcmd', None) is None:
                self.bootcmd = []

            if ind is None:
                ind = len(self.bootcmd)

            if freq is None:
                self.bootcmd.insert(ind, command)
            else:
                self.bootcmd.insert(
                    ind, ['cloud-init-per', freq,
                          "vmup-cust-%s" % len(self.bootcmd)] + command)

        else:
            raise ValueError("Cannot run command on '%s' -- "
                             "only 'boot' or None is supported" % when)

    # TODO: CA certs, resolv.conf, alter completion message, call url,
    #       reboot, ssh-keys, puppet?, timezone, etc


def make_cloud_init(hostname, user_data, outname='{hostname}-cidata.iso',
                    outdir='/var/lib/libvirt/images', pool=None,
                    net=None, overwrite=False):
    metadata = get_metadata(hostname, net=net)
    userdata = yaml.safe_dump(user_data.__getstate__())

    with tempfile.TemporaryDirectory('cloud-init-work-') as tmpdir:
        with open(os.path.join(tmpdir, 'meta-data'), 'x') as mdf:
            mdf.write(metadata)

        with open(os.path.join(tmpdir, 'user-data'), 'x') as udf:
            udf.write("#cloud-config\n")
            udf.write(userdata)


        if pool is None:
            output_path = os.path.join(outdir,
                                       outname.format(hostname=hostname))
            return disk_helpers.make_iso_file(
                output_path, 'cidata', "user-data", "meta-data",
                overwrite=overwrite, cwd=tmpdir)
        else:
            
            return disk_helpers.make_iso_volume(
                pool, outname, 'cidata', "user-data",
                "meta-data", overwrite=overwrite, cwd=tmpdir)
