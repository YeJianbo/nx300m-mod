"""Build read-only IPCS observer and verify it against an isolated stock Gamma node.

No running camera namespace is entered or restarted. The probe uses original
NX300M 1.15 ARM libraries with explicit parameters and allocated register RAM.
"""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess

from elftools.elf.elffile import ELFFile
from inspect_gamma_hook import Stock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime'
PROBE = ROOT/'outputs/metol/isp-color-observer-probe'
GUEST = '/opt/nx300m-lab/stock-1.15'


def abi_evidence():
    config = json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))
    rootfs = Path(config['rootfs'])
    if not rootfs.is_absolute(): rootfs = ROOT/rootfs
    udd = Stock(rootfs/'usr/lib/libudd.so.3.1')
    include = ROOT/'work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd'
    header = (include/'ipcs_submodule.h').read_text(encoding='utf-8',errors='replace')
    types = (include/'ipcs_submodule_type.h').read_text(encoding='utf-8',errors='replace')
    required = ['void ipcs_tone_lut(unsigned short *p_lut);',
                'enum ipcs_param_error ipcs_color_correction_param(struct ipcs_color_correction *p_info);',
                'enum ipcs_param_error ipcs_rgb2ycc_param(struct ipcs_rgb2ycc *p_info);']
    for declaration in required:
        if declaration not in header: raise ValueError(f'ABI declaration missing: {declaration}')
    if 'unsigned int col_mat[9];' not in types:
        raise ValueError('Vendor CCM nine-uint32 layout changed')
    anchors = {0x9559c:('add','r3, r3, #0x4000'),
               0x955a8:('mov','r2, #0x4000'),
               0x91254:('ldr','r2, [r3, r2, lsl #2]'),
               0x91294:('cmp','r3, #9')}
    for address,expected in anchors.items():
        instruction = udd.instruction(address)
        if (instruction.mnemonic,instruction.op_str) != expected:
            raise ValueError(f'Original setter ABI instruction changed at {address:#x}')
    return {'vendor_header':'work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/ipcs_submodule.h:37,64',
            'vendor_type':'ipcs_submodule_type.h:26-30,95-97',
            'original_udd':str(udd.path),'gamma_words':8192,'gamma_word_type':'uint16',
            'gamma_original_copy_bytes':0x4000,'ccm_words':9,
            'ccm_storage_type':'uint32; optional int32 bit view is not a color-domain claim',
            'anchors':{hex(k):list(v) for k,v in anchors.items()}}


def build():
    OUT.mkdir(parents=True,exist_ok=True)
    target = OUT/'isp-color-observer.so'
    compiler = shutil.which('clang')
    if not compiler: raise RuntimeError('clang is required')
    command = [compiler,'--target=arm-linux-gnueabi','-march=armv7-a','-mfloat-abi=soft',
               '-std=c99','-O2','-Wall','-Wextra','-Werror','-fPIC','-ffreestanding',
               '-fno-builtin','-fno-stack-protector','-nostdlib','-shared','-fuse-ld=lld',
               '-Wl,--hash-style=sysv','runtime/isp_color_observer.c','-o',str(target)]
    result = subprocess.run(command,cwd=ROOT,capture_output=True,timeout=60)
    (OUT/'isp-color-observer-build.log').write_bytes(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError(f'Observer build exit {result.returncode}: {result.stderr.decode("utf-8","replace")}')
    with target.open('rb') as stream:
        elf = ELFFile(stream)
        if elf['e_machine'] != 'EM_ARM' or elf.elfclass != 32 or not elf.little_endian:
            raise ValueError('Observer output is not ARM32 little-endian')
        exports = {s.name for s in elf.get_section_by_name('.dynsym').iter_symbols()
                   if s['st_shndx'] != 'SHN_UNDEF'}
    if not {'ipcs_tone_lut','ipcs_color_correction_param','ipcs_rgb2ycc_param','nx_isp_color_observer_directory'} <= exports:
        raise ValueError('Required observer exports missing')
    return {'output':str(target.relative_to(ROOT)), 'command':command,'exit_code':0}


def probe():
    PROBE.mkdir(parents=True,exist_ok=True)
    base = '/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    script = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
base={shlex.quote(base)}
cd "$base"
readlink -f "$rf/usr/lib/libudd.so" "$rf/usr/lib/libudd.so.3.1" \\
 > outputs/metol/isp-color-observer-probe/udd-provider-realpaths.txt
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie \\
 -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start \\
 -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/isp_color_observer_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so \\
 -Wl,-l:libdiosal.so -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
 -o outputs/metol/isp-color-observer-probe/probe.arm
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
 mount --make-rprivate /
 mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs /var/run
 exec "$1/qemu-arm-static" -cpu cortex-a9 -L "$1" \\
  -E LD_LIBRARY_PATH="$1/usr/lib:$1/lib" \\
  -E LD_PRELOAD="$2/outputs/firmware-runtime/isp-color-observer.so" \\
  "$2/outputs/metol/isp-color-observer-probe/probe.arm" \\
  "$2/outputs/metol/isp-color-observer-probe"
' sh "$rf" "$base"
'''
    result = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',script],
                            cwd=ROOT,capture_output=True,timeout=60)
    (PROBE/'stdout.log').write_bytes(result.stdout)
    (PROBE/'stderr.log').write_bytes(result.stderr)
    stdout,stderr = result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace')
    if result.returncode:
        raise RuntimeError(f'Observer stock-node probe exit {result.returncode}: {stderr.strip()}\n{stdout.strip()}')
    if 'ISP_COLOR_OBSERVER_PROBE=PASS' not in stdout:
        raise RuntimeError('Observer probe has no PASS marker')
    provider_paths = (PROBE/'udd-provider-realpaths.txt').read_text(encoding='utf-8').splitlines()
    if len(provider_paths) != 2 or len(set(provider_paths)) != 1:
        raise ValueError('libudd.so and libudd.so.3.1 do not resolve to the same original provider')
    samples = []
    for mode in range(2):
        record = json.loads((PROBE/f'gamma-mode-{mode}.json').read_text(encoding='utf-8'))
        raw = (PROBE/f'gamma-mode-{mode}.mmio.bin').read_bytes()
        expected = list(struct.unpack('<8192H',raw))
        if record['values_uint16'] != expected or record['word_count'] != 8192:
            raise ValueError('Observed Gamma differs from original UDD full-table copy')
        if (record['next_provider'] not in [GUEST+'/usr/lib/libudd.so',GUEST+'/usr/lib/libudd.so.3.1']
                or not record['next_provider_returned']
                or record['caller_library'] != GUEST+'/usr/lib/libiq-fw-slpcam.so'
                or record['caller_relative_offset'] != '0x33cd38'
                or record['next_function_relative_offset'] != '0x95548'):
            raise ValueError('Observer did not forward to the original UDD provider')
        samples.append({'mode_selector':mode,'sequence':record['sequence'],'words':8192,
                        'minimum':min(expected),'maximum':max(expected),
                        'observer_equals_udd_mmio':True,'original_iq_gamma_call_edge_verified':True,
                        'observed_json':str((PROBE/f'gamma-mode-{mode}.json').relative_to(ROOT))})
    accepted = json.loads((PROBE/'ccm-input.json').read_text(encoding='utf-8'))
    rejected = json.loads((PROBE/'ccm-rejected.json').read_text(encoding='utf-8'))
    values = [256,-101,3,63,255,-65,-3,33,257]
    if (accepted['values_uint32'] != [v & 0xffffffff for v in values]
            or accepted['values_int32_bit_view'] != values or accepted['return_code'] != 0
            or rejected['values_uint32'][0] != 1024 or rejected['return_code'] != 2):
        raise ValueError('Observer CCM values or original error return differ')
    ycc = json.loads((PROBE/'rgb2ycc-input.json').read_text(encoding='utf-8'))
    if ycc['values_uint32'] != [128,0,0,0,128,0,0,0,128] or ycc['return_code'] != 0:
        raise ValueError('RGB-to-YCC observed coefficients or return differ')
    return {'exit_code':0,'gamma_samples':samples,'ccm_original_return_preserved':True,
            'rgb2ycc_original_mmio_and_input_preserved':True,
            'ccm_input_bits_preserved':True,'ccm_original_mmio_packing_verified':True,
            'stdout':stdout.strip().splitlines(),'stderr':stderr.strip().splitlines(),
            'libraries':['unmodified NX300M 1.15 libiq-fw-slpcam.so',
                         'unmodified NX300M 1.15 libudd.so.3.1'],
            'udd_alias_realpath':provider_paths[0],
            'storage_fixture':'allocated Gamma buffer and IPCS register RAM; original Gamma node and setters execute',
            'synthetic_parameters':True,'active_capture':False,'image_verified':False,
            'main_simulator_restarted':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only',action='store_true')
    args = parser.parse_args()
    report = {'abi':abi_evidence(),'build':build(),
              'runtime':{'activation':'load .so; no additional switch',
                         'directory':'/var/run/nx300m-isp-color-<pid>-XXXXXX; mode0700',
                         'retention':'8 atomic JSON ring slots per setter; full raw argument values',
                         'capture_boundary':'input before RTLD_NEXT; next provider recorded',
                         'forwarding':'identical argument pointer and original return code/errno; capture failures do not suppress calls',
                         'contention':'skip snapshot instead of waiting; original call still runs',
                         'calibration_requirement':'disable color-modifying preload shims to observe stock-to-UDD values'},
              'probe':probe() if not args.build_only else {'executed':False}}
    output = OUT/'isp-color-observer-build.json'
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))


if __name__ == '__main__':
    main()
