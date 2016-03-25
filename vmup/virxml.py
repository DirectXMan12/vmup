import xmlmapper as mp
from xmlmapper import xml_helpers as xh


def _none_str(v):
    return str(v) if v is not None else None


def _from_dict():
    def _loads(elem):
        return dict(elem.attrib)

    def _dumps(val, elem):
        elem.attrib.clear()
        elem.attrib.update(val)
        return elem

    return mp.Custom(_loads, _dumps)


def _optional_tag_with_attr(attr):
    def _loads(elem):
        return elem.get(attr)

    def _dumps(val, elem):
        if val is None:
            return None
        else:
            elem.set(attr, val)
            return elem

    return mp.Custom(_loads, _dumps)


def _unit_loader():
    def _loads(elem):
        return "%s %s" % (elem.text, elem.attrib['unit'])

    def _dumps(v, elem):
        parts = v.split(" ")
        elem.text = parts[0]
        if len(parts) > 1:
            elem.attrib['unit'] = parts[1]

        return elem

    return mp.Custom(_loads, _dumps)


def _split_loader(*parts):
    def _loads(elem):
        return ':'.join(elem.attrib[part] for part in parts)

    def _dumps(val, elem):
        vals = val.split(':')
        for i, part in enumerate(parts):
            elem.attrib[part] = vals[i]

        return elem

    return mp.Custom(_loads, _dumps)


class Disk(mp.Model):
    ROOT_ELEM = 'disk'

    device_type = mp.ROOT % _split_loader('type', 'device')

    driver = mp.ROOT.driver % _split_loader('name', 'type')
    source_file = mp.ROOT.source['file']
    source_vol = mp.ROOT.source % _split_loader('pool', 'volume')
    target = mp.ROOT.target % _split_loader('bus', 'dev')
    read_only = mp.ROOT.readonly % mp.Custom(xh.load_presence,
                                             xh.dump_presence)


class Filesystem(mp.Model):
    ROOT_ELEM = 'filesystem'

    fs_type = mp.ROOT['type']
    access_mode = mp.ROOT['accessmode']

    source_dir = mp.ROOT.source['dir']
    target_name = mp.ROOT.target['dir']

    read_only = mp.ROOT.readonly % mp.Custom(xh.load_presence,
                                             xh.dump_presence)


class Interface(mp.Model):
    ROOT_ELEM = 'interface'

    iface_type = mp.ROOT['type']

    source = mp.ROOT.source % _from_dict()
    target = mp.ROOT.target % _optional_tag_with_attr('dev')

    virtualport = mp.ROOT.virtualport % _optional_tag_with_attr('type')

    mac_address = mp.ROOT.mac['address']
    model_type = mp.ROOT.model['type']


class Domain(mp.Model):
    ROOT_ELEM = 'domain'

    uuid = mp.ROOT.uuid
    name = mp.ROOT.name
    memory = mp.ROOT.memory % _unit_loader()
    cpus = mp.ROOT.vcpu

    disks = mp.ROOT.devices[...].disk % Disk
    filesystems = mp.ROOT.devices[...].filesystem % Filesystem
    interfaces = mp.ROOT.devices[...].interface % Interface


class VolumeTarget(mp.Model):
    ROOT_ELEM = 'target'

    fmt = mp.ROOT.format['type']

    owner = mp.ROOT.permissions.owner % (int, _none_str)
    perms = mp.ROOT.permissions.mode

class Volume(mp.Model):
    ROOT_ELEM = 'volume'

    vol_type = mp.ROOT['type']

    name = mp.ROOT.name

    allocation = mp.ROOT.allocation % _unit_loader()
    capacity = mp.ROOT.capacity % _unit_loader()

    target = mp.ROOT.target % VolumeTarget % {'always_present': True}
    backing_file = mp.ROOT.backingStore.path
    backing_fmt = mp.ROOT.backingStore.format['type']

