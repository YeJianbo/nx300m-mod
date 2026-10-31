"""Attach a persistent FAT32 lab card inside the stock mount namespace.

Only a new lab image is formatted. Existing images are reused, never reset.
The original libdevman/libstorage still read card properties and filesystem.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import struct
import subprocess

from session_stock import GUEST, PROJECT, inspect_session

IMAGE = Path('/opt/nx300m-lab/media/nx300m-card-1g.img')
IMAGE_BYTES = 1024*1024*1024
PARTITION_START = 2048


def bootstrap(guest, image=IMAGE):
    import ctypes
    import fcntl
    guest = Path(guest)
    if str(guest) != GUEST or not (guest/'.stock-source').is_file():
        raise ValueError('Expected the prepared stock lab root')
    if (guest/'var/run/nx300m-sdcard.json').exists():
        raise RuntimeError('A card is already configured in this namespace')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    libc.mount.restype = ctypes.c_int

    def mount(source, target, filesystem, flags=0, options=None):
        if libc.mount(os.fsencode(source), os.fsencode(target), os.fsencode(filesystem), flags,
                      os.fsencode(options) if options is not None else None):
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(target))

    image = Path(image)
    if not image.is_absolute(): raise ValueError('Lab card image path must be absolute')
    image.parent.mkdir(parents=True, exist_ok=True)
    with image.with_suffix('.lock').open('a') as ownership:
        fcntl.flock(ownership, fcntl.LOCK_EX|fcntl.LOCK_NB)
        created = not image.exists()
        if created:
            mbr = bytearray(512)
            struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\xfe\xff\xff', 0x0c, b'\xfe\xff\xff',
                             PARTITION_START, IMAGE_BYTES//512-PARTITION_START)
            mbr[510:512] = b'\x55\xaa'
            with image.open('xb') as stream:
                stream.truncate(IMAGE_BYTES); stream.write(mbr)
            subprocess.run(['/usr/sbin/mkfs.fat', '-F', '32', '--offset', str(PARTITION_START), '-h', str(PARTITION_START), '-n', 'NX300MLAB', str(image)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not image.is_file() or image.stat().st_size != IMAGE_BYTES:
            raise ValueError('Lab card image has an unexpected type or size')
        with image.open('rb') as stream:
            mbr = stream.read(512)
            stream.seek(PARTITION_START*512); boot = stream.read(512)
        if mbr[450] != 0x0c or struct.unpack_from('<II', mbr, 454) != (PARTITION_START, IMAGE_BYTES//512-PARTITION_START):
            raise ValueError('Existing card has an unexpected partition layout; it will not be reformatted')
        if boot[82:90] != b'FAT32   ' or boot[510:512] != b'\x55\xaa':
            raise ValueError('Existing lab image is not FAT32; it will not be reformatted')
        associated = subprocess.run(['/usr/sbin/losetup', '-j', str(image)], capture_output=True, text=True, check=True)
        if associated.stdout.strip(): raise RuntimeError('Lab card image is already attached: '+associated.stdout.strip())
        result = subprocess.run(['/usr/sbin/losetup', '--find', '--show', '--partscan', str(image)], capture_output=True, text=True, check=True)
        loop = Path(result.stdout.strip())
        mode = loop.stat()
        if not stat.S_ISBLK(mode.st_mode): raise ValueError('Loop setup did not return a block device')
        loopfd = os.open(loop, os.O_RDWR|os.O_CLOEXEC)
        try:
            info = bytearray(232)
            fcntl.ioctl(loopfd, 0x4c05, info, True)  # LOOP_GET_STATUS64
            struct.pack_into('<I', info, 52, struct.unpack_from('<I', info, 52)[0]|4)  # LO_FLAGS_AUTOCLEAR
            fcntl.ioctl(loopfd, 0x4c04, info)       # LOOP_SET_STATUS64
            partition = Path('/sys/class/block')/(loop.name+'p1')/'dev'
            major, minor = map(int, partition.read_text().strip().split(':'))
            partition_device = os.makedev(major, minor)
            # Keep the only new block alias in this private /var/run, then bind
            # it onto an inert mountpoint. Other lab namespaces do not see it.
            nodes = guest/'var/run/nx300m-sdcard-nodes'
            nodes.mkdir()
            mount('tmpfs', nodes, 'tmpfs', 2, 'mode=0755')
            for name, number in (('mmcblk0', mode.st_rdev), ('mmcblk0p1', partition_device)):
                device = nodes/name
                os.mknod(device, stat.S_IFBLK|0o660, number)
                os.chown(device, 65534, 65534)
                alias = guest/'dev'/name
                if alias.exists() and not alias.is_file(): raise ValueError('Card mountpoint is not an inert file')
                alias.touch(exist_ok=True)
                mount(device, alias, 'none', 4096)  # MS_BIND
            partitions = guest/'var/run/nx300m-card-partitions'
            partitions.write_text('major minor  #blocks  name\n\n'
                                  f'{os.major(mode.st_rdev):4d} {os.minor(mode.st_rdev):5d} {IMAGE_BYTES//1024:9d} mmcblk0\n'
                                  f'{major:4d} {minor:5d} {(IMAGE_BYTES-PARTITION_START*512)//1024:9d} mmcblk0p1\n')
            mount(partitions, guest/'proc/partitions', 'none', 4096)
            sysfs = guest/'sys'
            sysfs.mkdir(exist_ok=True)
            mount('tmpfs', sysfs, 'tmpfs', 2|4, 'mode=0755')  # MS_NOSUID|MS_NODEV
            properties = {
                'devices/platform/jack/mmc_online': '1',
                'devices/platform/jack/usb_online': '0',
                'devices/platform/dw_mmc_sdcard.0/media_error': '0',
                'devices/platform/dw_mmc_sdcard.0/protect_card': '0',
                'devices/platform/dw_mmc_sdcard.0/support_card': '1',
                'devices/platform/d4keys-polled/shutter': '0',
            }
            for name, value in properties.items():
                target = sysfs/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value+'\n')
            bus = sysfs/'bus/platform/devices'
            bus.mkdir(parents=True, exist_ok=True)
            (bus/'jack').symlink_to('../../../devices/platform/jack')
            (guest/'mnt/mmc').mkdir(parents=True, exist_ok=True)
            report = {'image': str(image), 'created': created, 'bytes': IMAGE_BYTES,
                      'loop': str(loop), 'autoclear': True, 'source': '/dev/mmcblk0p1', 'target': '/mnt/mmc',
                      'partition_start_sector': PARTITION_START, 'partition_device': [major, minor],
                      'filesystem': 'vfat', 'card_detect_input': 1, 'physical_camera_modified': False}
            # Use the in-chroot source and destination names so the kernel's
            # /proc/mounts entry matches what the unmodified storage library asks.
            os.chroot(guest); os.chdir('/')
            mount('/dev/mmcblk0p1', '/mnt/mmc', 'vfat', 2|4, 'uid=65534,gid=65534,umask=0007')
            report['free_bytes'] = os.statvfs('/mnt/mmc').f_bavail*os.statvfs('/mnt/mmc').f_frsize
            Path('/var/run/nx300m-sdcard.json').write_text(json.dumps(report)+'\n')
            print(json.dumps(report), flush=True)
        finally:
            os.close(loopfd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootstrap', help=argparse.SUPPRESS)
    parser.add_argument('--image', default=str(IMAGE), help='Absolute WSL path to a separate persistent FAT32 lab image')
    args = parser.parse_args()
    if os.name == 'nt':
        state = inspect_session()
        if not state['running']: raise RuntimeError('Original camera is not running')
        script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/sdcard.py'
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(state['namespace_pid']), '--mount',
                                 'python3', script, '--bootstrap', GUEST, '--image', args.image], capture_output=True, timeout=30)
        if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
        print(result.stdout.decode('utf-8').strip())
        return
    if args.bootstrap != GUEST: parser.error('Run with --bootstrap inside the private stock mount namespace')
    bootstrap(args.bootstrap, args.image)


if __name__ == '__main__': main()
