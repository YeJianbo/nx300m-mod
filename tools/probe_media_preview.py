"""Compile the native preview extractor and check actual NX300M SRW/JPEG bytes."""
import io
import json
from pathlib import Path
import shlex
import struct
import subprocess

from PIL import Image
from build_ycc_probe import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/metol/media-preview'
DRIVER = r'''
#include "media_preview.h"
int main(int argc,char **argv) {
    if(argc!=4) return 64;
    unsigned char *data=NULL;size_t length=0;mp_preview_info info;
    int result=mp_extract(argv[1],atoi(argv[2]),&data,&length,&info);
    printf("{\"result\":%d,\"bytes\":%zu,\"source\":\"%s\",\"offset\":%llu,"
        "\"width\":%u,\"height\":%u,\"orientation\":%u,\"candidates\":%u,\"embedded\":%d}\n",
        result,length,info.source?info.source:"",(unsigned long long)info.file_offset,
        info.width,info.height,info.orientation,info.candidate_count,info.embedded);
    if(!result) {
        FILE *file=fopen(argv[3],"wb");
        if(!file) {free(data);return 65;}
        int ok=fwrite(data,1,length,file)==length;
        if(fclose(file)) ok=0;
        free(data);if(!ok) return 66;
    } else if(data || length || info.source) return 67;
    return 0;
}
'''


def run_command(args, log, timeout=45):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    (OUT / log).write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'{log}: exit {result.returncode}: '
                           + result.stderr.decode('utf-8', 'replace')[-3000:])
    return result


def fixture_tiff(endian, jpeg, invalid=False):
    # Root -> SubIFD -> JPEG. Both byte orders use the same bounded directory walk.
    u16 = lambda n: struct.pack(endian+'H', n)
    u32 = lambda n: struct.pack(endian+'I', n)
    entry = lambda tag, value: u16(tag)+u16(4)+u32(1)+u32(value)
    header = (b'II' if endian == '<' else b'MM') + u16(42)+u32(8)
    root = u16(1)+entry(330, 26)+u32(0)
    child = u16(2)+entry(513, 0xfffffff0 if invalid else 56)+entry(514, len(jpeg))+u32(0)
    return header+root+child+jpeg


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    driver = OUT/'driver.c'
    driver.write_text(DRIVER, encoding='utf-8')
    base, output = wsl_path(ROOT), wsl_path(OUT)
    host = f'cd {shlex.quote(base)}\ngcc -std=gnu99 -O2 -Wall -Wextra -Werror -D_FILE_OFFSET_BITS=64 -I mod/metol/remote {shlex.quote(output+"/driver.c")} -o {shlex.quote(output+"/probe.host")}\n'
    run_command(['wsl.exe', '-e', 'sh', '-c', host], 'host-build.log')
    arm = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 \\
 -include mod/metol/remote/compat.h -I"$tc/include" -I mod/metol/remote -mfloat-abi=soft \\
 -c {shlex.quote(output+"/driver.c")} -o {shlex.quote(output+"/probe.arm.o")}
'''
    run_command(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', arm], 'arm-build.log')
    reference = json.loads((ROOT/'outputs/looks/photo-reference.json').read_text(encoding='utf-8'))
    sample = io.BytesIO()
    Image.new('RGB', (16, 12), (32, 64, 128)).save(sample, format='JPEG')
    jpeg = sample.getvalue()
    for name, data in [('little-subifd', fixture_tiff('<', jpeg)),
                       ('big-subifd', fixture_tiff('>', jpeg)),
                       ('invalid-range', fixture_tiff('<', jpeg, True)),
                       ('no-preview', b'II*\0'+struct.pack('<IHI', 8, 0, 0)),
                       ('unsupported-video', b'\0\0\0\x18ftypmp42')]:
        (OUT/(name+'.bin')).write_bytes(data)
    cases = []

    def check(name, path, small, expected, source=None, size=None):
        path = Path(path)
        target = OUT/(name+'.jpg')
        result = run_command(['wsl.exe', '-e', output+'/probe.host', wsl_path(path),
                              str(int(small)), wsl_path(target)], name+'.log')
        value = json.loads(result.stdout.decode('utf-8'))
        assert value['result'] == expected, (name, value)
        if source is not None:
            assert value['source'] == source, (name, value)
        if expected == 0:
            data = target.read_bytes()
            with path.open('rb') as original:
                original.seek(value['offset'])
                assert original.read(value['bytes']) == data, name
            with Image.open(io.BytesIO(data)) as image:
                assert list(image.size) == [value['width'], value['height']], name
                if size is not None:
                    assert list(image.size) == size, (name, image.size)
                image.verify()
            value['byte_identical_to_source_range'] = True
            value['pillow_verified'] = True
        value.update(name=name, path=str(path), small=small)
        cases.append(value)

    for small in (True, False):
        check('srw-'+('grid' if small else 'full'), reference['raw']['path'], small, 0,
              'tiff-embedded-jpeg', [160, 120] if small else reference['size'])
        check('jpeg-'+('grid' if small else 'full'), reference['external_jpeg']['path'], small, 0,
              'jpeg-exif-thumbnail' if small else 'original-jpeg',
              [160, 120] if small else reference['size'])
    check('jpeg-no-exif', ROOT/reference['output'], True, 0, 'original-jpeg')
    check('little-subifd', OUT/'little-subifd.bin', True, 0, 'tiff-embedded-jpeg', [16, 12])
    check('big-subifd', OUT/'big-subifd.bin', True, 0, 'tiff-embedded-jpeg', [16, 12])
    check('invalid-range', OUT/'invalid-range.bin', True, -2)
    check('no-preview', OUT/'no-preview.bin', True, 1)
    check('unsupported-video', OUT/'unsupported-video.bin', True, 2)
    report = {'host_build_exit_code': 0, 'arm_compile_exit_code': 0, 'passed': True,
              'cases': cases,
              'scope': 'Production header, host execution, actual NX300M SRW/JPEG byte ranges; '
                       'ARM object compilation. Does not exercise HTTP/cache or camera execution.',
              'scaled': False}
    (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
