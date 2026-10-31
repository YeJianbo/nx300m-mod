"""Build the optional stock-ABI JPEG decoder; optionally run one ARM image probe.

No simulator restart, image installation, encoder build or media-state changes.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/firmware-runtime/jpeg-decode'

PROBE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <jpeglib.h>
#include "jpeg_cpu_adapter.h"
extern int nx_decode(void *,struct nx300m_jpeg_task *) __asm__("_ZN7DscJfif10JpegDecodeER7sJpegOp");
extern int nx300m_jpeg_query_cbcr(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t *,uint32_t *);
static struct { unsigned char *base;uint32_t size; } maps[4];
void *nx300m_map_virtual_access(uint32_t address,uint32_t minimum,uint32_t *remaining,int required) {
    if (remaining) *remaining=0;
    for (unsigned i=0;i<4;i++) {
        if (required != (i ? 2 : 1)) continue;
        uint32_t base=(uint32_t)(uintptr_t)maps[i].base;
        if (address>=base && address-base<maps[i].size && minimum<=maps[i].size-(address-base)) {
            if (remaining) *remaining=maps[i].size-(address-base);
            return (void *)(uintptr_t)address;
        }
    }
    return NULL;
}
static int untouched(void) {
    for (unsigned i=1;i<4;i++) if (maps[i].base)
        for (uint32_t n=0;n<maps[i].size+16;n++) if (maps[i].base[n]!=0xa5) return 0;
    return 1;
}
static int test(const unsigned char *original,unsigned long length,int pack,int split) {
    struct jpeg_decompress_struct ref;struct jpeg_error_mgr error;
    memset(&ref,0,sizeof ref);ref.err=jpeg_std_error(&error);jpeg_create_decompress(&ref);
    jpeg_mem_src(&ref,(unsigned char *)original,length);if (jpeg_read_header(&ref,TRUE)!=JPEG_HEADER_OK) return 10;
    uint32_t w=ref.image_width,h=ref.image_height,v=ref.comp_info[0].v_samp_factor,cw=(w+1)/2,ch=(h+v-1)/v;
    if (ref.num_components!=3 || ref.jpeg_color_space!=JCS_YCbCr || ref.comp_info[0].h_samp_factor!=2 || (v!=1 && v!=2)) return 11;
    uint32_t pitches[3]={w+(pack?3:8),(pack?cw:2*cw)+(pack?5:8),cw+5};
    uint32_t rows[3]={h,ch,ch},widths[3]={w,pack?cw:2*cw,cw};
    for (unsigned i=1;i<4;i++) {
        maps[i].size=(i==3 && !pack)?0:pitches[i-1]*rows[i-1];
        maps[i].base=maps[i].size?malloc(maps[i].size+16):NULL;
        if (maps[i].size && !maps[i].base) return 12;
        if (maps[i].base) memset(maps[i].base,0xa5,maps[i].size+16);
    }
    struct nx300m_jpeg_task task;memset(&task,0,sizeof task);
    task.width=17;task.height=19;task.ycc_type=99;task.size=0x13579bdf;task.quality=0x2468;task.timeout=2000;
    task.y_even=(uint32_t)(uintptr_t)maps[1].base;task.cb_even=(uint32_t)(uintptr_t)maps[2].base;
    task.cr_even=(uint32_t)(uintptr_t)maps[3].base;
    if (split) {
        task.y_odd=task.y_even+pitches[0];task.cb_odd=task.cb_even+pitches[1];
        if (pack) task.cr_odd=task.cr_even+pitches[2];
    }
    task.stride_y=pitches[0]*(split?2:1);task.stride_c=pitches[1]*(split?2:1);
    /* Both chroma planes have one stride in this ABI. */
    if (pack) {pitches[2]=pitches[1];}
    task.output=(uint32_t)(uintptr_t)maps[0].base;task.pack_type=pack;
    if (nx_decode(NULL,&task)!=1 || task.width!=w || task.height!=h || task.ycc_type!=(v==2) || task.size!=0x13579bdf || task.quality!=0x2468) return 13;
    if (!pack && split) {
        uint32_t c0=0,c1=0;
        if (nx300m_jpeg_query_cbcr(w,h,pitches[0],task.ycc_type,task.y_even,task.y_odd,
                task.cb_even+pitches[0],task.cb_odd+pitches[0],&c0,&c1)!=1 || c0!=task.cb_even || c1!=task.cb_odd) return 24;
        c0=c1=0xdeadbeef;
        if (nx300m_jpeg_query_cbcr(w,h,pitches[0],task.ycc_type,task.y_even+1,task.y_odd,
                task.cb_even+pitches[0],task.cb_odd+pitches[0],&c0,&c1)!=0 || c0!=0xdeadbeef || c1!=0xdeadbeef) return 25;
    }
    ref.out_color_space=JCS_YCbCr;ref.do_fancy_upsampling=FALSE;ref.dct_method=JDCT_ISLOW;
    if (!jpeg_start_decompress(&ref)) return 14;
    unsigned char *line=malloc(w*3);if (!line) return 15;
    for (uint32_t y=0;y<h;y++) {
        JSAMPROW row=line;if (jpeg_read_scanlines(&ref,&row,1)!=1) return 16;
        for (uint32_t x=0;x<w;x++) {
            unsigned char yy=maps[1].base[(size_t)y*pitches[0]+x];
            size_t at=(size_t)(y/v)*pitches[1]+(pack?x/2:2*(x/2));
            unsigned char cb=maps[2].base[at],cr=pack?maps[3].base[(size_t)(y/v)*pitches[2]+x/2]:maps[2].base[at+1];
            if (yy!=line[3*x] || cb!=line[3*x+1] || cr!=line[3*x+2]) {
                fprintf(stderr,"pixel mismatch pack=%d split=%d x=%u y=%u\n",pack,split,x,y);return 17;
            }
        }
    }
    if (!jpeg_finish_decompress(&ref)) return 18;
    jpeg_destroy_decompress(&ref);free(line);
    for (unsigned i=1;i<4;i++) if (maps[i].base) {
        for (uint32_t row=0;row<rows[i-1];row++)
            for (uint32_t x=widths[i-1];x<pitches[i-1];x++)
                if (maps[i].base[(size_t)row*pitches[i-1]+x]!=0xa5) return 19;
        for (unsigned guard=0;guard<16;guard++) if (maps[i].base[maps[i].size+guard]!=0xa5) return 20;
        memset(maps[i].base,0xa5,maps[i].size+16);
    }
    struct nx300m_jpeg_task before=task;
    uint32_t input_size=maps[0].size;maps[0].size=6;
    if (nx_decode(NULL,&task)!=0 || memcmp(&before,&task,sizeof task) || !untouched()) return 21;
    maps[0].size=input_size;
    unsigned char saved=maps[0].base[5];maps[0].base[5]=1;
    if (nx_decode(NULL,&task)!=0 || memcmp(&before,&task,sizeof task) || !untouched()) return 22;
    maps[0].base[5]=saved;
    uint32_t y_size=maps[1].size;maps[1].size=(h-1)*pitches[0]+w-1;
    int rejected=nx_decode(NULL,&task)==0 && !memcmp(&before,&task,sizeof task);
    maps[1].size=y_size;if (!rejected || !untouched()) return 23;
    fprintf(stderr,"probe passed pack=%d split=%d %ux%u ycc=%u\n",pack,split,w,h,v==2);
    for (unsigned i=1;i<4;i++) {free(maps[i].base);maps[i].base=NULL;maps[i].size=0;}
    return 0;
}
int main(int argc,char **argv) {
    if (argc!=2) return 2;
    FILE *f=fopen(argv[1],"rb");if (!f || fseek(f,0,SEEK_END)) return 3;
    long bytes=ftell(f);if (bytes<4 || bytes>64*1024*1024-8 || fseek(f,0,SEEK_SET)) return 4;
    unsigned char *original=malloc(bytes),*input=malloc(bytes+8);
    if (!original || !input || fread(original,1,bytes,f)!=(size_t)bytes) return 5;
    fclose(f);
    /* APP13 contains a false EOI. Its length must take precedence over FFD9. */
    memcpy(input,"\xff\xd8\xff\xed\x00\x06\x00\xff\xd9\x00",10);memcpy(input+10,original+2,bytes-2);
    maps[0].base=input;maps[0].size=bytes+8;
    int result=test(original,bytes,0,1);if (!result) result=test(original,bytes,1,1);
    free(original);free(input);if (result) {fprintf(stderr,"probe failed code=%d\n",result);return result;}
    puts("{\"pack0_split\":true,\"pack1_split\":true,\"stock_libjpeg_pixels_equal\":true,\"app_payload_eoi_ignored\":true,\"short_input_rejected\":true,\"invalid_marker_rejected\":true,\"short_output_rejected\":true,\"failed_task_unchanged\":true,\"completed_layout_match\":true,\"foreign_layout_rejected\":true}");
    return 0;
}
'''


def wsl_path(path: Path) -> str:
    path = path.resolve()
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def stock_headers() -> None:
    """Preserve IJG headers; generate only the independently verified extension.

    Stock libjpeg.so.8.3.0 jpeg_CreateDecompress@0xec70 requires 0x1f8 bytes;
    @0xed28..0xed34 it clears four words at 0x38/3c/40/44. Core dimensions
    @0x160b0 uses scale_num/denom at 0x30/34, output dimensions at 0x80/84,
    comp_info at 0xe8, component stride 0x58. Raw-data and output setup also
    confirm raw_data_out 0x54, fancy 0x5c, scanline 0x9c, vertical factors
    0x14c/154. Evidence: outputs/firmware-runtime/jpeg-decode-abi.{json,asm}.
    The four words' semantics are unknown; they remain vendor-reserved.
    """
    source = ROOT/'work/reference/jpeg8-headers'
    target = OUT/'jpeg8-stock'
    target.mkdir(exist_ok=True)
    for name in ('jconfig.h', 'jmorecfg.h', 'jerror.h'):
        shutil.copyfile(source/name, target/name)
    header = (source/'jpeglib.h').read_text(encoding='utf-8')
    before, after = header.split('struct jpeg_decompress_struct {', 1)
    needle = '  unsigned int scale_num, scale_denom; /* fraction by which to scale image */'
    if after.count(needle) != 1:
        raise RuntimeError('Unexpected IJG decompressor header')
    after = after.replace(needle, needle+'\n\n  /* Stock ARM vendor extension, zeroed by jpeg_CreateDecompress. */\n'
                          '  unsigned int nx_stock_vendor_reserved[4];', 1)
    (target/'jpeglib.h').write_text(before+'struct jpeg_decompress_struct {'+after, encoding='utf-8', newline='\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Run the bounded standalone ARM probe')
    parser.add_argument('--jpeg', type=Path, default=ROOT/'outputs/nxconnect/download-check/SAM_4262.JPG')
    args = parser.parse_args()
    if args.probe and not args.jpeg.is_file():
        parser.error(f'JPEG does not exist: {args.jpeg}')
    OUT.mkdir(parents=True, exist_ok=True)
    stock_headers()
    (OUT/'probe.c').write_text(PROBE, encoding='utf-8', newline='\n')
    script = f'''set -eu
cd {shlex.quote(wsl_path(ROOT))}
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
cc="$tc/bin/arm-linux-gnueabi-gcc"
base="-B$tc/lib/gcc-cross/arm-linux-gnueabi/14/ -std=gnu99 -O2 -Wall -Wextra -march=armv7-a -mfloat-abi=soft -fno-stack-protector -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -Ioutputs/firmware-runtime/jpeg-decode/jpeg8-stock -Iruntime"
link="-nostdlib -Wl,--allow-shlib-undefined -Wl,-rpath-link,$rf/usr/lib -Wl,-rpath-link,$rf/lib"
out=outputs/firmware-runtime/jpeg-decode
"$cc" $base $link -shared -fPIC -Wl,-soname,jpeg-decode.so runtime/jpeg_decode.c \\
 "$rf/usr/lib/libjpeg.so.8" "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" -lgcc -o "$out/jpeg-decode.so"
"$cc" $base $link -no-pie -fno-pic -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 "$out/probe.c" runtime/jpeg_decode.c work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/usr/lib/libjpeg.so.8" "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" -lgcc -o "$out/jpeg-decode-probe.arm"
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'sh', '-s'], input=script.encode(), capture_output=True)
    (OUT/'build.stdout.log').write_bytes(result.stdout)
    (OUT/'build.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        print(result.stderr.decode('utf-8', 'replace'))
        return result.returncode
    records = []
    for name in ('jpeg-decode.so', 'jpeg-decode-probe.arm'):
        data = (OUT/name).read_bytes()
        elf = ELFFile(io.BytesIO(data))
        version_section = elf.get_section_by_name('.gnu.version_r')
        versions = [a.name for _, aux in version_section.iter_versions() for a in aux] if version_section else []
        if any(v.startswith('GLIBC_') and tuple(map(int, v[6:].split('.'))) > (2, 13) for v in versions):
            raise RuntimeError(f'{name} requires a newer libc: {versions}')
        dynamic = elf.get_section_by_name('.dynamic')
        needed = [tag.needed for tag in dynamic.iter_tags() if tag.entry.d_tag == 'DT_NEEDED']
        symbols = elf.get_section_by_name('.dynsym')
        if name.endswith('.so'):
            if ('libjpeg.so.8' not in needed or not symbols.get_symbol_by_name('_ZN7DscJfif10JpegDecodeER7sJpegOp')
                    or not symbols.get_symbol_by_name('nx300m_jpeg_query_cbcr')):
                raise RuntimeError('Missing stock JPEG dependency or original decode ABI export')
        records.append({'file': name, 'bytes': len(data), 'machine': elf['e_machine'], 'needed': needed, 'versions': versions})
    report = {'artifacts': records, 'layout': 'software convention: pack0 Y/CbCr; pack1 Y/Cb/Cr',
              'jpeg_decompress_abi': '504 bytes; independently verified 16-byte vendor extension after scale_denom',
              'hardware_bitexact_layout_verified': False, 'input_boundary': 'tracked virtual mapping + JPEG marker EOI',
              'probe_run': False, 'simulator_restarted': False}
    if args.probe:
        command = ['/opt/nx300m-lab/stock-1.15/qemu-arm-static', '-cpu', 'cortex-a9', '-L', '/opt/nx300m-lab/stock-1.15',
                   wsl_path(OUT/'jpeg-decode-probe.arm'), wsl_path(args.jpeg)]
        probe = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'env', '-u', 'LD_PRELOAD', '-u', 'LD_AUDIT', *command],
                               capture_output=True, timeout=120)
        (OUT/'probe.stdout.log').write_bytes(probe.stdout)
        (OUT/'probe.stderr.log').write_bytes(probe.stderr)
        report.update(probe_run=True, probe_exit=probe.returncode, source=str(args.jpeg.resolve()))
        if probe.returncode == 0:
            report['probe'] = json.loads(probe.stdout)
        else:
            print(probe.stderr.decode('utf-8', 'replace'))
        (OUT/'build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        (OUT/'probe-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report))
        return probe.returncode
    (OUT/'build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
