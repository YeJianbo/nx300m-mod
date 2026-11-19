"""The deployed, checked ABI7 preview combination used by this installer."""
import hashlib


PREFIX = 'nx300m-mod/'
MARKERS = ('IPCS_OPEN_DIAGNOSTIC', 'IPCS_SESSION_PREVIEW')
# Match the ABI7 Session build and its checked production wrapper, not ABI4.
BACKEND_FILES = {
    'ipcs-diagnostic/lib/smartfilter_ram.bin': '61e8fc1f8d8d9a25bdccb1e42e3a3d5297c1ee6c43be84e7c692eee64458254d',
    'ipcs-diagnostic/bin/smartfilter_loader.arm': '6cce90e62c596d11ca3a2e0c532bd7f7bcb4045dbfec4d4dda136fac45fe57a9',
    'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
    'smartfilter-preview.sh': '6fc627df3ef2bd8579db9163c825ddfd9cb873f54914eb7914742447e37f9e2c',
}
BACKEND_PROFILES = {
    'v0.1.0-preview.12': {
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '0421789035ac3fd73a631a08c3e8b1a560135e1fd9256074aad943c91249ebd2',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': 'e8b94000c6b60ba3b7d52196017190a11e9c2c44928228248bf7d75dfac95f32',
        'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
        'smartfilter-preview.sh': '4350c21c162276b842cb0c69b9466cce21aecdd5799d2c282fef802ec82610c6',
    },
    'v0.1.0-preview.11': {
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '674bf2897f66347cdd7332a495fc8a7e34aea30b105de85dead2ae1bb26fee8f',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': 'e1edaddfd147afa6d3f8a1e21d28d204318d4da60fdc092ae53ca83aea16dc45',
        'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
        'smartfilter-preview.sh': '6170539b4d1c60d472495fcb91432bdb742021d49c5e190df671652f4d6d7915',
    },
    # Frozen from the user-verified preview.9 card on 2026-11-15.
    'abi7-user-verified-card': {
        **BACKEND_FILES,
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '63ef3dee420041b64b9ee45df2a0046c95caa7578d7ecc85f41e56c28c8a0d14',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': '0ff0348d6a328db8135b1487456bd84b829c353c020d95892ccac1519ffcb667',
        'smartfilter-preview.sh': 'fd5e616069a60453512e188fddcfb833ffc86522614cc62597cc127f62f1f38f',
    },
    'abi7-capture-handoff': {
        **BACKEND_FILES,
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '5f669d9325b9d79f8ec2dba1459cda2376119051f6ea72d705098c37afbba991',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': '409ed3970c5bc5c69b6eaff939849ac6cb774393c469953c41824fa03d0c7287',
    },
    'abi7-live-preview': BACKEND_FILES,
    'v0.1.0-preview.5': {
        **BACKEND_FILES,
        'smartfilter-preview.sh': '22848bb1f34c48d14111facaf0812049879c355491f0e8129dfdd7beb2042bf8',
    },
    # The public preview.4 first-install candidate was built before the zcode
    # wrapper/engine update and must be validated against its own release set.
    'v0.1.0-preview.4': {
        'ipcs-diagnostic/lib/smartfilter_ram.bin': '61e8fc1f8d8d9a25bdccb1e42e3a3d5297c1ee6c43be84e7c692eee64458254d',
        'ipcs-diagnostic/bin/smartfilter_loader.arm': '6cce90e62c596d11ca3a2e0c532bd7f7bcb4045dbfec4d4dda136fac45fe57a9',
        'ipcs-diagnostic/bin/smartfilter_probe_launcher.arm': '19c9b2d8150baa29f4d2b287331b0450ff5d429364d5f2e2a973eb9fecb4e632',
        'smartfilter-preview.sh': 'dd6dc0d62757ca6a1821783a2e2071d5d7d5a9b212fdcc4fc55f03206d11f357',
    },
}
BACKEND_BUILDS = {name: 3392396409 for name in BACKEND_PROFILES}
BACKEND_BUILDS['v0.1.0-preview.12'] = 578882475
BACKEND_BUILDS['v0.1.0-preview.11'] = 3580398190
BACKEND_BUILDS['abi7-capture-handoff'] = 345225401
BACKEND_BUILDS['abi7-user-verified-card'] = 2965073795


def resolve_backend_profile(read):
    try:
        hashes = {name: hashlib.sha256(read(PREFIX + name)).hexdigest()
                  for name in BACKEND_FILES}
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('Session 预览后端资源缺失。') from exc
    matches = [(name, files) for name, files in BACKEND_PROFILES.items()
               if files == hashes]
    if len(matches) != 1:
        raise ValueError('无法识别 Session 预览后端版本。')
    return matches[0]


def verify_backend(read, files=None, profile=None):
    """Read through the installer's safe path checks, or a staged update view."""
    if files is None:
        if profile is None:
            profile, files = resolve_backend_profile(read)
        else:
            files = BACKEND_PROFILES.get(profile)
            if files is None:
                raise ValueError('未知 Session 预览后端版本：' + profile)
    else:
        if profile is None:
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
    if profile not in BACKEND_BUILDS and profile != 'card-manifest':
        raise ValueError('Session 预览后端没有对应的 build 记录。')
    return {'profile': profile, 'abi': 7, 'build': BACKEND_BUILDS.get(profile), 'session': 1,
            'timing': 0, 'calltrace': 0}
