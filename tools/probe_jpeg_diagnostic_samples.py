"""Check missing/short diagnostic planes without a camera or real photographs."""
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime/jpeg-encode/diagnostic-samples-probe'
STOCK = '/opt/nx300m-lab/stock-1.15'


def wsl(path):
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    # Unique fixture directories avoid changing old evidence or guest media.
    import tempfile
    fixtures = Path(tempfile.mkdtemp(prefix='jpeg-samples-',dir=OUT))
    cases = [('missing-y',None,None),('missing-cbcr',128,None),
             ('short-cbcr',128,64),('long-cbcr',128,129),('valid',128,128)]
    for name,y,c in cases:
        directory = fixtures/name
        directory.mkdir()
        if y is not None: (directory/'sample_16x8_y.bin').write_bytes(bytes(range(128)))
        if c is not None: (directory/'sample_16x8_cbcr.bin').write_bytes(bytes([128])*c)
    script = f'''set -eu
cd {shlex.quote(wsl(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf={STOCK}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft -fno-stack-protector \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -Iwork/reference/jpeg8-headers -Iruntime \\
 -Ioutputs/firmware-runtime/jpeg-encode -include outputs/firmware-runtime/jpeg-encode/stock_libc_compat.h \\
 -nostdlib -no-pie -fno-pic -nostartfiles -Wl,-E -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 runtime/jpeg_diagnostic_sample_probe.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libdl.so.2" "$rf/lib/libc-2.13.so" -lgcc \\
 -o outputs/firmware-runtime/jpeg-encode/diagnostic-samples-probe/probe.arm
'''
    built = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'build.stderr.log').write_bytes(built.stderr)
    if built.returncode:
        print(built.stderr.decode('utf-8','replace'))
        raise SystemExit(built.returncode)
    records=[]
    for name,_,_ in cases:
        result = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','env','-u','LD_PRELOAD','-u','LD_AUDIT',
            STOCK+'/qemu-arm-static','-cpu','cortex-a9','-L',STOCK,wsl(OUT/'probe.arm'),
            wsl(fixtures/name),'success' if name=='valid' else 'failure'],capture_output=True,timeout=15)
        (OUT/(name+'.stdout.log')).write_bytes(result.stdout)
        (OUT/(name+'.stderr.log')).write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name}: exit {result.returncode}: {result.stderr.decode("utf-8","replace")}')
        prefix='[JPEG DIAGNOSTIC] '
        log=next(json.loads(line[len(prefix):]) for line in result.stderr.decode().splitlines() if line.startswith(prefix))
        if log['sample_injected'] != (name=='valid'):
            raise AssertionError(f'{name}: inaccurate sample_injected')
        records.append({'case':name,'exit_code':result.returncode,'result':json.loads(result.stdout),'diagnostic':log})
    report={'cases':records,'source':'generated 16x8 test planes only','camera_started':False,'real_photos_written':False}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
