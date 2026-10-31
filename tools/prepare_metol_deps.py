"""Extract an isolated Debian ARM build toolchain, with apt package checksums."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
PACKAGES = ('binutils-arm-linux-gnueabi', 'cpp-14-arm-linux-gnueabi', 'gcc-14-arm-linux-gnueabi', 'gcc-arm-linux-gnueabi', 'libgcc-14-dev-armel-cross', 'libc6-dev-armel-cross', 'linux-libc-dev-armel-cross', 'libx11-dev', 'x11proto-dev', 'libxft-dev', 'libfreetype-dev', 'libfontconfig-dev', 'libxrender-dev')

def main():
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'apt-get', '--print-uris', '-y', '--download-only', 'install', 'gcc-arm-linux-gnueabi', 'libx11-dev', 'libxft-dev', 'libxrender-dev'], capture_output=True, text=True, check=True)
    packages = []
    for url, name, size, checksum in re.findall(r"'([^']+)' (\S+) (\d+) MD5Sum:(\w+)", result.stdout):
        if any(name.startswith(p + '_') for p in PACKAGES):
            packages.append(dict(url=url, name=name, size=int(size), apt_md5=checksum))
    if len(packages) != len(PACKAGES): raise RuntimeError('Incomplete apt toolchain package list')
    output = PROJECT / 'work/metol-deps'
    output.mkdir(exist_ok=True)
    def fetch(package):
        path = output / package['name']
        url = package['url'].replace('http://deb.debian.org/debian/', 'https://mirrors.tuna.tsinghua.edu.cn/debian/')
        data = path.read_bytes() if path.exists() else urllib.request.urlopen(url, timeout=45).read()
        if len(data) != package['size'] or hashlib.md5(data).hexdigest() != package['apt_md5']: raise RuntimeError('Package verification failed: ' + package['name'])
        path.write_bytes(data)
        print(package['name'], flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(fetch, packages))
    (output / 'SOURCE.json').write_text(json.dumps({'apt_metadata': True, 'mirror': 'https://mirrors.tuna.tsinghua.edu.cn/debian/', 'packages': packages}, indent=2), encoding='utf-8')
    source = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:] + '/work/metol-deps'
    for package in packages:
        subprocess.run(['wsl.exe', '-u', 'root', '-e', 'dpkg-deb', '-x', source + '/' + package['name'], '/opt/nx300m-toolchain'], check=True)
    print('Isolated toolchain: /opt/nx300m-toolchain', flush=True)

if __name__ == '__main__': main()
