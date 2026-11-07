"""Shared payload contract for the previously deployed no-lens Wi-Fi gate."""
import hashlib
import json
import re


PREFIX = 'nx300m-lens-debug/'
BINARY_SHA256 = '1147521d95b41214e859530174d6a970c9f38b2a263708ccf14907dcbe903d83'
SCRIPT_SHA256 = 'b4304fcd45c8d894f23ceca289adda88169e0ae720597a6267f537d280167b35'
RUN_ID = b'wifi-20261107-restored\n'
FILES = ('autoexec-wifi.sh', 'manual-lens-wifi-mem.arm', 'WIFI_MEM_RUN_ID', 'WIFI_MEM_PROFILE')


def verify_wifi_payload(read, *, allow_stock=False):
    try:
        if hashlib.sha256(read(PREFIX + FILES[0])).hexdigest() != SCRIPT_SHA256:
            raise ValueError('无镜头无线启动脚本版本不匹配。')
        if hashlib.sha256(read(PREFIX + FILES[1])).hexdigest() != BINARY_SHA256:
            raise ValueError('无镜头无线补丁版本不匹配。')
        if not re.fullmatch(rb'[0-9A-Za-z-]{1,64}\n', read(PREFIX + FILES[2])):
            raise ValueError('无镜头无线运行标识无效。')
        profiles = (b'manual-lens-wifi\n', b'stock\n') if allow_stock else (b'manual-lens-wifi\n',)
        if read(PREFIX + FILES[3]) not in profiles:
            raise ValueError('无镜头无线开机配置缺失。')
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('安装包缺少无镜头无线资源：' + str(exc)) from exc
    return {'boot_profile': 'manual-lens-wifi', 'binary_sha256': BINARY_SHA256}


def build_wifi_payload(root):
    directory = root / 'outputs/controls/manual-lens'
    evidence = json.loads((directory / 'wifi-mem-build-probe.json').read_text(encoding='utf-8'))
    binary = (directory / FILES[1]).read_bytes()
    if evidence['fixture_returncode'] != 0 or evidence['stderr'] or evidence['bytes'] != len(binary):
        raise ValueError('无镜头无线补丁缺少有效构建记录。')
    script = (root / 'mod/deployment/mfw/autoexec-lens-wifi-mem.sh').read_bytes().replace(b'\r\n', b'\n')
    files = dict(zip((PREFIX + name for name in FILES),
                     (script, binary, RUN_ID, b'manual-lens-wifi\n')))
    verify_wifi_payload(files.__getitem__)
    return files
