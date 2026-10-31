"""Check the observer's mapping guard and static electronic-state decoder.

Uses synthetic mappings and the stock ELF segment metadata; no camera access.
"""
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/controls/manual-lens'
FIXTURE = r'''
#define NX300M_LENS_PROBE_NO_START 1
#include "runtime/manual_lens_probe.c"
int main(void) {
    const uint32_t bias = 0xb4500000u;
    biases[1] = bias; bias_found[1] = 1; map_count = 2;
    maps[0] = (struct mapping){bias+0x3bf000u,bias+0x3d8000u,0,1,0,1,0};
    maps[1] = (struct mapping){bias+0x3d8000u,bias+0x467000u,0,1,0,-1,1};
    if (!readable(bias+PROBE_IQ_SINGLETON,4,1)) return 1;
    if (!readable(bias+PROBE_PP_SINGLETON,4,1)) return 2;
    if (!readable(bias+0x3bf000u,4,1)) return 3;
    if (!readable(bias+0x3d7ffeu,4,1)) return 4;
    /* Page padding outside PT_LOAD p_memsz must stay inaccessible. */
    if (readable(bias+0x466e20u,4,1)) return 5;
    maps[1].anonymous = 0; maps[1].module = 2;
    if (readable(bias+PROBE_IQ_SINGLETON,4,1)) return 6;
    maps[1].anonymous = 1; maps[1].module = -1; maps[1].readable = 0;
    if (readable(bias+PROBE_IQ_SINGLETON,4,1)) return 7;
    if (readable(0xfffffffcu,8,-1)) return 8;
    maps[0].anonymous = 1; maps[0].module = -1;
    if (readable(bias+0x3bf000u,4,1)) return 9;
    /* Signed negative states use wrapped A32 ADD followed by unsigned BHI. */
    const uint32_t raw[] = {0xfffffffcu,0xfffffffdu,0xfffffffeu,0xffffffffu,
                            0u,11u,12u,13u,18u,19u,20u,0x7fffffffu,0x80000000u};
    const uint32_t expected[] = {0x20000018u,0x2000001bu,0x2000001au,0x20000017u,
                                0x20000018u,0x20000018u,0x20000019u,0x20000018u,
                                0x20000018u,0x20000019u,0x20000018u,0x20000018u,0x20000018u};
    for (unsigned int i=0; i<ARRAY_SIZE(raw); i++)
        if (lens_module_result(raw[i]) != expected[i]) return 10 + (int)i;
    printf("{\"passed\":22,\"mapping_cases\":9,\"decode_cases\":13,\"scope\":\"synthetic_mapping_guard_and_static_state_decode\"}\n");
    return 0;
}
'''


def main():
    fixture = OUT / 'observer-bss-fixture.c'
    fixture.write_text(FIXTURE, encoding='ascii')
    base = '/mnt/' + ROOT.drive[0].lower() + ROOT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
cc -std=c99 -O2 -Wall -Wextra -Werror -fno-builtin -I. \\
 -Ioutputs/controls/manual-lens outputs/controls/manual-lens/observer-bss-fixture.c \\
 -o outputs/controls/manual-lens/observer-bss-fixture
outputs/controls/manual-lens/observer-bss-fixture
sh -n mod/deployment/mfw/autoexec-lens-debug.sh
'''
    run = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-c', script],
                         capture_output=True, text=True, timeout=30)
    report = {'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
              'scope': 'production mapping guard with synthetic maps; static readiness decode; shell syntax',
              'camera_executed': False, 'physical_color_verified': False}
    (OUT / 'observer-bss-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    run.check_returncode()


if __name__ == '__main__':
    main()
