import crypt
import os.path
import random

import libvirt
from lxml import etree

from vmup import virxml as vx
from vmup import notacloud as nac
from vmup import disk as disk_helper


# avoid printing errors to stderr
libvirt.registerErrorHandler(lambda c, e: None, None)


class VM(vx.Domain):
    def __init__(self, hostname, image_dir='/var/lib/libvirt/images'):
        self._hostname = hostname
        self._img_dir = image_dir

        self._disk_cnt = 0

        self._net_config = []

        with open('template.xml') as templ:
            # use a different parser to ensure pretty-printing works
            parser = etree.XMLParser(remove_blank_text=True)
            super(VM, self).__init__(etree.fromstring(templ.read(),
                                                      parser=parser))

        self.name = hostname

        self.userdata = nac.UserData()

    def inject_file(self, dest_path, content, permissions=None, **kwargs):
        # TODO: gzip large files and use the gzip encoding?
        if permissions is not None:
            kwargs['permissions'] = permissions

        self.userdata.add_file(dest_path, content, **kwargs)

    def configure_user(self, name=None, password=None, groups=None,
                       authorized_keys=None, **kwargs):
        if name is None:
            # configure the default user
            # TODO: configure groups for default user?
            self.userdata.allow_ssh_password_auth = (password is not None)
            self.userdata.add_default_user(password=password,
                                           authorized_keys=authorized_keys)
        else:
            # use a custom user
            args = {}
            if password is None:
                args['lock_password'] = True
            else:
                args['lock_password'] = False
                args['password_hash'] = crypt.crypt(password)

            args['groups'] = groups
            args['ssh_authorized_keys'] = authorized_keys
            args['sudo'] = kwargs.get('sudo', ['ALL=(ALL) NOPASSWD:ALL'])

            self.userdata.add_user(name, **args)

        # don't force the user to immediately reset the password
        self.userdata.set_passwords(expire=False)

    def finalize(self, recreate_ci=False):
        if self._net_config:
            self._net_config.extend(['auto lo', 'iface lo inet loopback'])

        self._make_cloud_init(overwrite=recreate_ci)
        return self.to_xml(pretty_print=True, encoding=str)

    def launch(self, xml=None, redefine=None, start=True, conn_uri=None):
        if xml is None:
            xml = self.to_xml(pretty_print=True, encoding=str)

        conn = libvirt.open(conn_uri)

        try:
            dom = conn.lookupByName(self.name)
        except libvirt.libvirtError as ex:
            if ex.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                dom = None
            else:
                raise

        if redefine and dom is not None:
            dom.undefine()
            dom = None

        if dom is None:
            dom = conn.defineXML(xml)

        if start and dom is not None:
            dom.create()

        conn.close()

    def provision_disk(self, name, size, backing_file=None,
                       fmt='qcow2', overwrite=False):
        disk_helper.make_disk_file(
            self._main_disk_path(name, fmt), size,
            os.path.join(self._img_dir, backing_file), fmt,
            overwrite=overwrite)
        disk = self._main_disk_conf(name, fmt)
        self.disks.append(disk)

    def share_directory(self, source_path, dest_path, name=None):
        if name is None:
            name = os.path.basename(source_path)

        conf = self._fs_conf(source_path, name)
        self.filesystems.append(conf)

        # NB: the cloud-init mounts module ensures the target dir exists
        self.userdata.add_mount(name, dest_path, '9p',
                                'trans=virtio,version=9p2000.L', '0', '2')

        # TODO: check permissions/selinx on host dir?

    def add_symlink(self, target, linkname, permissions=None):
        # TODO: file bug with cloud-init to get a symlink module?
        self.run_command(['ln', '-s', target, linkname])
        if permissions is not None:
            self.run_command(['chmod', permissions, linkname])

    def run_command(self, command):
        self.userdata.run_command(command)

    def configure_networking(self, fmt, **kwargs):
        mac = kwargs.get('mac')
        if mac is None:
            mac = self._gen_mac_addr()

        if fmt == 'default':
            conf = self._default_net_conf(kwargs.pop('network', 'default'),
                                          mac, kwargs.pop('portgroup', None))
        elif fmt == 'ovs':
            conf = self._ovs_net_conf(kwargs.pop('bridge'), mac)
        else:
            raise ValueError("Unknown networking type '%s'" % fmt)

        self.interfaces.append(conf)

        self._set_net_config(**{k.replace('-', '_'): v
                                for k, v in kwargs.items()})

    def _next_disk(self):
        disk = chr(self._disk_cnt + ord('a'))
        self._disk_cnt += 1
        return disk

    def _main_disk_path(self, name, fmt):
        return os.path.join(self._img_dir, "%s-%s.%s" % (self.name, name, fmt))

    def _ci_disk_conf(self):
        ci_disk = vx.Disk()
        ci_disk.device_type = 'file:cdrom'
        ci_disk.driver = 'qemu:raw'
        ci_disk.source_file = os.path.join(self._img_dir,
                                           '%s-cidata.iso' % self._hostname)
        ci_disk.target = 'ide:hd%s' % self._next_disk()
        ci_disk.read_only = True

        return ci_disk

    def _main_disk_conf(self, name, fmt='qcow2'):
        disk = vx.Disk()
        disk.device_type = 'file:disk'
        disk.driver = 'qemu:%s' % fmt
        disk.target = 'virtio:vd%s' % self._next_disk()
        disk.source_file = self._main_disk_path(name, fmt)

        self._disk_cnt += 1

        return disk

    def _fs_conf(self, path, name):
        fs = vx.Filesystem()
        fs.fs_type = 'mount'
        fs.access_mode = 'squash'
        fs.source_dir = path
        fs.target_name = name
        fs.read_only = True

        return fs

    def _default_net_conf(self, network, mac, portgroup=None):
        iface = vx.Interface()
        iface.iface_type = 'network'
        iface.mac_address = mac

        src = {'network': network}
        if portgroup is not None:
            src['portgroup'] = portgroup

        iface.source = src
        iface.model_type = 'virtio'

        return iface

    def _ovs_net_conf(self, bridge, mac):
        iface = vx.Interface()
        iface.iface_type = 'bridge'
        iface.mac_address = mac
        iface.source = {'bridge': bridge}
        iface.virtualport = 'openvswitch'
        iface.model_type = 'virtio'

        return iface

    def _set_net_config(self, device=None, ip=None, gateway=None,
                        broadcast=None, bootproto=None, dns_search=None,
                        mac=None, nameservers=None, auto=True, ipv6=False,
                        netmask=None):
        if device is None:
            device = 'eth0'

        if auto:
            self._net_config.append("auto %s" % device)

        if bootproto is None and ip is not None:
            bootproto = 'static'

        net_num = '' if not ipv6 else '6'
        self._net_config.append(
            "iface {device} inet{net_num} {bootproto}".format(
                device=device, net_num=net_num, bootproto=bootproto))

        if bootproto == 'static':
            self._net_config.append('    address %s' % ip)

            # TODO: do a better job with ipv6 here
            if netmask is None:
                # assume a reasonable netmask default
                if not ipv6:
                    netmask = '255.255.255.0'

            self._net_config.append('    netmask %s' % netmask)

            if gateway is None:
                if not ipv6:
                    # assume a reasonable gateway default
                    gateway = '.'.join(ip.split('.')[:3]) + '.1'

            self._net_config.append('    gateway %s' % gateway)

            if dns_search is not None:
                self._net_config.append('    dns-search %s' % gateway)

            if nameservers is not None:
                if not isinstance(nameservers, str):
                    nameservers = ' '.join(nameservers)

                self._net_config.append('    dns-nameservers %s' % nameservers)

            if broadcast is not None:
                self._net_config.append('    broadcast %s' % broadcast)

    def _make_cloud_init(self, overwrite=False):
        nac.make_cloud_init(self._hostname, self.userdata,
                            outdir=self._img_dir, overwrite=overwrite,
                            net=self._net_config)
        self.disks.append(self._ci_disk_conf())

    def _gen_mac_addr(self):
        # TODO: make this deterministic?
        raw_mac = [0x52, 0x54, 0x00,
                   random.randint(0, 255),
                   random.randint(0, 255),
                   random.randint(0, 255)]

        return ':'.join("{:02x}".format(b) for b in raw_mac)