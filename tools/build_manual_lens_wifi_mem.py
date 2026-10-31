"""Build the bounded external Wi-Fi byte tool and its isolated QEMU fixture."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from build_manual_lens_wifi import write_wifi_stock_header

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/controls/manual-lens'
FIXTURE = r'''
#define NX_WIFI_MEM_FIXTURE 1
#define pwrite64 fake_pwrite64
#include "runtime/manual_lens_wifi_mem.c"
#undef pwrite64
static unsigned char image[sizeof nx_wifi_gate_stock];
static unsigned int fail_call,fail_from,io_calls,illegal;
int fake_pwrite64(int fd,const void *buffer,unsigned int count,long long offset) {
    (void)fd; io_calls++;
    if(count!=1 || (offset!=wifi_sites[0] && offset!=wifi_sites[1])) { illegal++; return -1; }
    if(fail_call==io_calls || (fail_from && io_calls>=fail_from)) return 0;
    image[(unsigned int)offset-NX_WIFI_GATE_ADDRESS]=*(const unsigned char *)buffer;
    return 1;
}
static int identity(void) { return 1; }
static int verify(unsigned char state[2]) {
    unsigned char copy[sizeof image]; memcpy(copy,image,sizeof image);
    for(unsigned int i=0;i<2;i++) state[i]=copy[wifi_sites[i]-NX_WIFI_GATE_ADDRESS];
    return normalize_wifi(copy);
}
static void reset(void) {
    memcpy(image,nx_wifi_gate_stock,sizeof image);
    writes_attempted=rollback_attempted=rollback_verified=io_calls=fail_call=fail_from=illegal=0;
    after_verified=0; failure_errno=0; failed_site=-1; failure_reason="none";
}
int fixture_main(void) {
    const struct wifi_ops ops={verify,identity};
    for(unsigned int mask=0;mask<4;mask++) {
        reset();
        for(unsigned int i=0;i<2;i++) if(mask&(1u<<i)) image[wifi_sites[i]-NX_WIFI_GATE_ADDRESS]=wifi_new[i];
        if(!wifi_transaction(0,&ops) || io_calls) return 10;
    }
    reset();
    if(!wifi_transaction(1,&ops) || io_calls!=2 || illegal) return 11;
    if(!wifi_transaction(1,&ops) || io_calls!=2) return 12;
    if(!wifi_transaction(2,&ops) || io_calls!=4 || memcmp(image,nx_wifi_gate_stock,sizeof image)) return 13;
    if(!wifi_transaction(2,&ops) || io_calls!=4) return 14;
    reset(); image[0]^=1;
    if(wifi_transaction(1,&ops) || io_calls) return 15;
    reset(); image[wifi_sites[0]-NX_WIFI_GATE_ADDRESS]=0xff;
    if(wifi_transaction(1,&ops) || io_calls) return 16;
    reset(); fail_call=2;
    if(wifi_transaction(1,&ops) || !rollback_attempted || !rollback_verified ||
       memcmp(image,nx_wifi_gate_stock,sizeof image) || illegal) return 17;
    if(write_site(2,0x0a) || write_site(0,0xff) || illegal) return 18;
    reset(); fail_from=2;
    if(wifi_transaction(1,&ops) || !rollback_attempted || rollback_verified || !after_verified ||
       after_wifi[0]!=wifi_new[0] || after_wifi[1]!=0x0a) return 19;
    return 0;
}
__asm__(".text\n.arm\n.global _start\n.type _start,%function\n_start:\nbl fixture_main\nmov r7,#1\nsvc #0\n");
'''


def main():
    compiler = shutil.which('clang')
    if not compiler:
        raise SystemExit('clang is required')
    evidence = write_wifi_stock_header(OUT)
    if not (OUT / 'manual_lens_probe_stock.h').is_file():
        raise SystemExit('Build tools/build_manual_lens_probe.py first for the 14 stock identity checks')
    (OUT / 'wifi-mem-fixture.c').write_text(FIXTURE, encoding='ascii')
    for label, source in [('manual-lens-wifi-mem', 'runtime/manual_lens_wifi_mem.c'),
                          ('wifi-mem-fixture', str(OUT / 'wifi-mem-fixture.c'))]:
        subprocess.run([
            compiler, '--target=arm-linux-gnueabi', '-march=armv7-a', '-mfloat-abi=soft',
            '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
            '-Wno-unused-variable', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
            '-fno-pie', '-ffunction-sections', '-fdata-sections', '-I.', '-I' + str(OUT),
            '-c', source, '-o', str(OUT / (label + '.o')),
        ], cwd=PROJECT, check=True)
    base = '/mnt/' + PROJECT.drive[0].lower() + PROJECT.as_posix()[2:]
    script = f'''set -eu
cd {shlex.quote(base)}
guest=/opt/nx300m-lab/stock-1.15
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
for name in manual-lens-wifi-mem wifi-mem-fixture; do
 "$tc/bin/arm-linux-gnueabi-ld" -m armelf_linux_eabi --gc-sections -e _start \\
  --dynamic-linker /lib/ld-linux.so.3 -rpath-link "$guest/lib" \\
  "outputs/controls/manual-lens/$name.o" "$guest/lib/libc.so.6" \\
  -o "outputs/controls/manual-lens/$name.arm"
done
timeout 10 "$guest/qemu-arm-static" -L "$guest" outputs/controls/manual-lens/wifi-mem-fixture.arm
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-e', 'sh', '-c', script],
                            capture_output=True, timeout=30)
    target = OUT / 'manual-lens-wifi-mem.arm'
    report = {
        'artifact': str(target), 'bytes': target.stat().st_size if target.is_file() else None,
        'default': 'status (O_RDONLY)', 'mutations': 'explicit apply or restore, two condition bytes only',
        'function': evidence, 'required_stock_symbols': 14,
        'fixture_scope': 'production normalization and write/rollback transaction; simulated memory IO and identity',
        'fixture_checks': ['all four known byte combinations: status does not write', 'apply two bytes and idempotence',
                           'restore two bytes and idempotence', 'reject another modified function byte',
                           'reject unknown condition byte', 'short second write restores prior bytes',
                           'reject non-whitelisted site or value', 'failed rollback reports remaining partial state'],
        'fixture_returncode': result.returncode, 'stdout': result.stdout.decode('utf-8', 'replace'),
        'stderr': result.stderr.decode('utf-8', 'replace'),
        'kernel_source_evidence': {
            'source': 'NX300M GPL Linux 3.5.0; not a match proof for running device kernel',
            'write_path': ['fs/proc/base.c:677-707 PTRACE_MODE_ATTACH permission, no trace attach',
                           'fs/proc/base.c:710-771 mem_write -> access_remote_vm',
                           'mm/memory.c:3802-3873 get_user_pages(write=1,force=1) -> copy_to_user_page',
                           'arch/arm/mm/flush.c:112-160 memcpy -> flush_ptrace_access -> executable cache maintenance',
                           'arch/arm/mm/cache-v7.S:163-207 D-cache clean, I-cache/BTB invalidate, DSB/ISB'],
            'limitations': ['real kernel/permissions not tested', 'two writes are not a transaction',
                            'original threads continue running', 'short writes and rollback may fail',
                            'hibernation can preserve modified private pages; power-cycle rollback not guaranteed'],
        },
        'physical_device_accessed': False, 'deployed': False,
    }
    (OUT / 'wifi-mem-build-probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
