"""The deployed, checked ABI7 preview combination used by this installer."""
import hashlib


PREFIX = 'nx300m-mod/'
MARKERS = ('IPCS_OPEN_DIAGNOSTIC', 'IPCS_SESSION_PREVIEW')
# Match the ABI7 Session build and its checked production wrapper, not ABI4.
BACKEND_FILES = {
    'ipcs-diagnostic/lib/smartfilter_ram.bin': '61e8fc1f8d8d9a25bdccb1e42e3a3d5297c1ee6c43be84e7c692eee64458254d',
    'ipcs-diagnostic/bin/smartfilter_loader.arm': '6cce90e62c596d11ca3a2e0c532bd7f7bcb4045dbfec4d4dda136fac45fe57a9',
    'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
    'smartfilter-preview.sh': '22848bb1f34c48d14111facaf0812049879c355491f0e8129dfdd7beb2042bf8',
}
BACKEND_PROFILES = {
    'abi7-live-preview': BACKEND_FILES,
    # The public preview.4 first-install candidate was built before the zcode
    # wrapper/engine update and must be validated against its own release set.
    'v0.1.0-preview.4': {
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '61e8fc1f8d8d9a25bdccb1e42e3a3d5297c1ee6c43be84e7c692eee64458254d',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': '6cce90e62c596d11ca3a2e0c532bd7f7bcb4045dbfec4d4dda136fac45fe57a9',
        'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
        'smartfilter-preview.sh': 'dd6dc0d62757ca6a1821783a2e2071d5d7d5a9b212fdcc4fc55f03206d11f357',
    },
}


def resolve_backend_profile(read):
    wrapper = PREFIX + 'smartfilter-preview.sh'
    try:
        digest = hashlib.sha256(read(wrapper)).hexdigest()
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('Session 预览包装器缺失。') from exc
    matches = [(name, files) for name, files in BACKEND_PROFILES.items()
               if files['smartfilter-preview.sh'] == digest]
    if len(matches) != 1:
        raise ValueError('无法识别 Session 预览后端版本。')
    return matches[0]


def verify_backend(read, files=None):
    """Read through the installer's safe path checks, or a staged update view."""
    if files is None:
        profile, files = resolve_backend_profile(read)
    else:
        profile = next((name for name, candidate in BACKEND_PROFILES.items()
                        if candidate == files), 'explicit')
    runtime = read(PREFIX + 'runtime-files.list').decode('ascii').splitlines()
    if len(runtime) != len(set(runtime)):
        raise ValueError('运行文件清单存在重复项目。')
    for name in (*MARKERS, *files):
        if name not in runtime:
            raise ValueError('Session 预览运行文件清单缺失：' + name)
    for name in MARKERS:
        if read(PREFIX + name) != b'1\n':
            raise ValueError('ABI7 Session 预览选择标记缺失或错误：' + name)
    for name, expected in files.items():
        if hashlib.sha256(read(PREFIX + name)).hexdigest() != expected:
            raise ValueError('ABI7 Session 预览版本不匹配：' + name)
    return {'profile': profile, 'abi': 7, 'build': 3392396409, 'session': 1,
            'timing': 0, 'calltrace': 0}
