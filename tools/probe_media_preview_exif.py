"""Check inherited orientation=6 metadata without changing JPEG image data."""
import io
import json
from pathlib import Path
import shlex
import subprocess

from PIL import Image
from build_ycc_probe import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/metol/media-preview-exif'
DRIVER = r'''
#include <stdio.h>
#include "mp_preview_exif.h"
int main(int argc,char **argv) {
    if(argc!=3) return 64;
    FILE *file=fopen(argv[1],"rb");if(!file) return 65;
    if(fseek(file,0,SEEK_END)) return 66;
    long size=ftell(file);if(size<0 || fseek(file,0,SEEK_SET)) return 67;
    size_t length=(size_t)size;unsigned char *data=malloc(length);if(!data) return 68;
    if(fread(data,1,length,file)!=length) return 69;
    fclose(file);mp_preview_exif_info info;
    int result=mp_preview_apply_orientation(&data,&length,1,6,&info);
    printf("{\"result\":%d,\"metadata_changed\":%d,\"existing_orientation\":%u,"
           "\"effective_orientation\":%u,\"bytes\":%zu}\n",result,info.metadata_changed,
           info.existing_orientation,info.effective_orientation,length);
    if(result) {free(data);return 70;}
    file=fopen(argv[2],"wb");if(!file) return 71;
    int ok=fwrite(data,1,length,file)==length;if(fclose(file)) ok=0;
    free(data);return ok?0:72;
}
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'driver.c').write_text(DRIVER, encoding='utf-8')
    base, output = wsl_path(ROOT), wsl_path(OUT)
    scripts = {
        'host': f'cd {shlex.quote(base)}\ngcc -std=gnu99 -O2 -Wall -Wextra -Werror -I mod/metol/remote {shlex.quote(output+"/driver.c")} -o {shlex.quote(output+"/probe.host")}\n',
        'arm': f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 \\
 -include mod/metol/remote/compat.h -I"$tc/include" -I mod/metol/remote -mfloat-abi=soft \\
 -c {shlex.quote(output+"/driver.c")} -o {shlex.quote(output+"/probe.arm.o")}
'''}
    for name, script in scripts.items():
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-c', script], capture_output=True, timeout=45)
        (OUT/(name+'-build.log')).write_bytes(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name} compile exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
    # Use the actual camera EXIF thumbnail already extracted by probe_media_preview.
    original = (ROOT/'outputs/metol/media-preview/srw-grid.jpg').read_bytes()
    (OUT/'input.jpg').write_bytes(original)
    cases = []

    def check(name, source, expected_changed, expected_orientation):
        target = OUT/(name+'.jpg')
        result = subprocess.run(['wsl.exe', '-e', output+'/probe.host', wsl_path(source), wsl_path(target)],
                                capture_output=True, timeout=10)
        (OUT/(name+'.log')).write_bytes(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name}: exit {result.returncode}: {result.stderr.decode("utf-8", "replace")}')
        value = json.loads(result.stdout)
        assert value['metadata_changed'] == expected_changed, value
        assert value['effective_orientation'] == expected_orientation, value
        data = target.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            assert image.getexif().get(274) == expected_orientation
            assert image.size == (160, 120)
            # Metadata changes leave decoded pixels and encoded scan bytes intact.
            pixels = image.tobytes()
        with Image.open(io.BytesIO(original)) as image:
            assert image.tobytes() == pixels
        assert data[data.index(b'\xff\xda'):] == original[original.index(b'\xff\xda'):]
        if not expected_changed:
            assert data == source.read_bytes()
        value.update(name=name, encoded_scan_unchanged=True, decoded_pixels_unchanged=True)
        cases.append(value)
        return target

    once = check('inherit-six', OUT/'input.jpg', 1, 6)
    assert once.stat().st_size == len(original)+36
    check('keep-existing-six', once, 0, 6)
    # Existing valid orientation=1 wins; parent=6 must not add another transform.
    one = bytearray(once.read_bytes());one[30:32] = b'\x01\x00'
    (OUT/'existing-one.jpg').write_bytes(one)
    check('keep-existing-one', OUT/'existing-one.jpg', 0, 1)
    # An existing APP1 lacking orientation retains its other EXIF fields.
    exif = Image.Exif();exif[271] = 'SAMSUNG'
    app = exif.tobytes()
    source = OUT/'missing-tag.jpg'
    source.write_bytes(original[:2]+b'\xff\xe1'+(len(app)+2).to_bytes(2,'big')+app+original[2:])
    patched = check('append-ifd-six', source, 1, 6)
    with Image.open(patched) as image:
        assert image.getexif()[271] == 'SAMSUNG'
    report = {'passed': True, 'host_build_exit_code': 0, 'arm_compile_exit_code': 0,
              'cases': cases, 'scope': 'One actual camera thumbnail with inherited parent orientation=6; '
              'metadata insertion/preservation only, no HTTP or browser exercise.'}
    (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
