import collections
import ftplib
import logging
import os
import re
import requests
import subprocess
import urllib.parse as urlparse

LOG = logging.getLogger(__name__)

ImageInfo = collections.namedtuple('ImageInfo', ['full_name', 'version',
                                                 'fmt', 'compression'])


# HACKY STUFF TO GET THE LATEST RELEASE (THERE MUST BE A BETTER WAY TO DO THIS)
class FedoraImageFetcher(object):
    SUB_PATH = "{release}/Cloud/x86_64/Images"
    BASE_URL = ("https://download.fedoraproject.org/pub/fedora/linux/releases"
                "/{release}/Cloud/x86_64/Images/{image}")
    MIRROR_LIST_URL = "https://mirrors.fedoraproject.org/mirrorlist"
    NAME_RE_FORMAT = (r'^Fedora-Cloud-{image_type}-(\d+)-(\d{{8}})'
                      r'.x86_64.(\w+)(.(\w+))?$')

    def __init__(self, image_type='Base'):
        raw_re = self.NAME_RE_FORMAT.format(image_type=image_type)
        self.NAME_RE = re.compile(raw_re)

    def _get_mirror(self, proto='ftp'):
        resp = requests.get(self.MIRROR_LIST_URL,
                            params={'path': 'pub/fedora/linux/releases/'})
        # TODO: check resp validity
        mirror_list = [r for r in resp.text.split('\n') if r]

        mirror_url_raw = next(r for r in mirror_list
                              if r.startswith(proto + "://"))
        mirror_url = urlparse.urlparse(mirror_url_raw)

        return mirror_url

    def _get_available_releases(self, ftp, mirror_url):
        ftp.cwd(mirror_url.path)
        return list(ftp.nlst())

    def _get_latest_release(self, releases):
        releases_nums = sorted(int(r) for r in releases if r.isdigit())
        return releases_nums[-1]

    def get_cloud_images(self, release=None):
        mirror_url = self._get_mirror('ftp')
        ftp = ftplib.FTP(mirror_url.netloc)
        ftp.login()

        if release is None:
            releases = self._get_available_releases(ftp, mirror_url)
            release = self._get_latest_release(releases)

        ftp.cwd(os.path.join(mirror_url.path,
                             self.SUB_PATH.format(release=release)))
        file_matches = [(f, self.NAME_RE.match(f)) for f in ftp.nlst()]
        files = (ImageInfo(f, (m.group(1), m.group(2)), m.group(3), m.group(5))
                 for f, m in file_matches if m)

        ftp.close()

        return files

    def get_image(self, version=None, fmt='qcow2'):
        # TODO: warn on more than one version part
        # TODO: check if version has two parts and use the
        #       second as the compose date
        if version is not None:
            version = version[0]

        images = self.get_cloud_images(version)
        # TODO: throw error if missing
        img_info = next(i for i in images if i.fmt == fmt)
        return img_info

    def fetch(self, img_dir, image, release):
        img_url = self.BASE_URL.format(release=release, image=image)

        # TODO: print stdout/stderr on err
        LOG.debug("Running command %s to fetch image..." % ['wget', img_url])
        try:
            # TODO: figure out a good way to show a progress bar w/o
            #       writing directly to stdout
            subprocess.check_call(['wget', '--no-verbose', '--show-progress',
                                   '--continue', img_url], cwd=img_dir,
                                  universal_newlines=True)
        except subprocess.CalledProcessError as ex:
            # the CalledProcessError gets put in __cause__
            # stdout/stderr are printed above
            raise Exception("Image fetching failed: "
                            "exit code %s" % ex.returncode)

        return os.path.join(img_dir, image)

    def find_local_images(self, img_dir):
        all_files = os.listdir(img_dir)
        matches = ((img, self.NAME_RE.match(img)) for img in all_files)
        img_files = (ImageInfo(img, (m.group(1), m.group(2)),
                               m.group(3), m.group(5))
                     for img, m in matches if m)

        # image name, release, compose, format, compression_format
        return img_files

    def find_local_image(self, img_dir, version=None,
                         fmt=None, compression=False):
        release = None
        compose = None

        if version is not None:
            release = version[0]
            if len(version) > 1:
                compose = version[1]

        all_images = self.find_local_images(img_dir)

        res_imgs = all_images
        if fmt is not None:
            res_imgs = (img_info for img_info in res_imgs
                        if img_info.fmt == fmt)

        if not compression:
            res_imgs = (img_info for img_info in res_imgs
                        if img_info.compression is None)

        if version is not None:
            res_imgs = (img_info for img_info in res_imgs
                        if img_info.version[0] == str(release))
            if compose is not None:
                res_imgs = (img_info for img_info in res_imgs
                            if img_info.version[1] == str(compose))

        res_imgs = sorted(res_imgs,
                          key=lambda info: (info.version[0], info.version[1],
                                            info.compression is None))
        if len(res_imgs) == 0:
            return None
        else:
            img = res_imgs[-1]
            return img


_IMAGE_FETCHERS = {'fedora': FedoraImageFetcher('Base'),
                   'fedora-atomic': FedoraImageFetcher('Atomic')}


def fetch_image(name, image_dir, check_local=True):
    if name.startswith('/'):
        return name

    parts = name.split('-')
    image_type = parts[0]
    version = None
    if len(parts) > 1:
        version = parts[1:]

    if _IMAGE_FETCHERS.get(image_type, None) is not None:
        fetcher = _IMAGE_FETCHERS[image_type]

        if check_local:
            img_info = fetcher.find_local_image(image_dir, version=version)
            if img_info is not None:
                if img_info.compression is not None:
                    # TODO: warn and fall back to downloading?
                    raise NotImplementedError("Unable to handle compressed "
                                              "image %s" % img_info.full_name)

                return (img_info.fmt,
                        os.path.join(image_dir, img_info.full_name))

        img_info = fetcher.get_image(version)

        if img_info.compression is not None:
            raise NotImplementedError("Unable to handle compressed "
                                      "image %s" % img_info.full_name)

        return (img_info.fmt, fetcher.fetch(image_dir, img_info.full_name,
                                            img_info.version[0]))
    else:
        raise ValueError("Unknown image alias '%s'" % name)


def make_iso_file(output_path, volid, *source_files,
                  overwrite=False, cwd=None):
        command = ["genisoimage", "-output", output_path, "-volid", volid,
                   "-joliet", "-rock"]
        command.extend(source_files)

        if os.path.exists(output_path):
            if not overwrite:
                LOG.info("Cloud-init iso file '%s' exists, "
                         "not recreating..." % output_path)
                return

            LOG.info("Cloud-init iso file '%s' exists, "
                     "deleting to recreate..." % output_path)
            os.remove(output_path)

        LOG.debug("Running command %s to create cloud-init "
                  "iso file..." % command)
        try:
            subprocess.check_call(command, stdout=subprocess.PIPE,
                                  cwd=cwd, stderr=subprocess.PIPE,
                                  universal_newlines=True)

        except subprocess.CalledProcessError as ex:
            # the CalledProcessError gets put in __cause__
            raise Exception("cloud-init iso file creation "
                            "failed: %s" % res.stderr)


def make_disk_file(path, size, backing_file=None,
                   fmt='qcow2', overwrite=False):
    command = ['qemu-img', 'create', '-f', fmt]
    if backing_file is not None:
        command.extend(['-o', 'backing_file=%s' % backing_file])

    command.append(path)
    command.append(size)

    if os.path.exists(path):
        if not overwrite:
            LOG.info("Disk file '%s' exists, not recreating..." % path)
            return None

        LOG.info("Disk file '%s' exists, deleting to "
                 "recreate..." % output_path)
        os.remove(path)

    LOG.debug("Running command %s to create disk..." % command)
    try:
        subprocess.check_call(command, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    except subprocess.CalledProcessError as ex:
        # the CalledProcessError gets put in __cause__
        raise Exception("Disk creation command failed: %s" % ex.stderr)
