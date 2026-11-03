"""Execute the fetched upstream CPU kernels locally, never their camera daemon."""
from pathlib import Path
import json
import hashlib
import subprocess

import numpy as np

from lut_resample import grid, sample_cube

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT/'work/reference/lut-quality'

NEW_MAIN = r'''
#include "nx300m_preview_rgb.h"
#include "nx300m_lut_grid_host.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char **argv) {
    if(argc!=5) return 2;
    unsigned n=(unsigned)strtoul(argv[1],0,10);
    if(n<2||n>65) return 2;
    FILE *f=fopen(argv[2],"rb"); if(!f) return 3;
    size_t nodes=(size_t)n*n*n;
    nx_rgb12 *lut=malloc(nodes*sizeof *lut); if(!lut) return 3;
    if(fread(lut,sizeof *lut,nodes,f)!=nodes||fgetc(f)!=EOF) return 3;
    fclose(f); f=fopen(argv[3],"rb"); if(!f) return 3;
    if(fseek(f,0,SEEK_END)) return 3;
    long bytes=ftell(f); if(bytes<=0||bytes%3||bytes>16000000) return 3;
    rewind(f); unsigned char *in=malloc(bytes),*out=malloc(bytes);
    if(!in||!out||fread(in,1,bytes,f)!=(size_t)bytes) return 3;
    fclose(f); nx_preview_processor p; if(nx_preview_init(&p)) return 4;
    if(n==18) {
        /* Exact upstream RGB888 adapter, in rows within its width contract. */
        size_t count=(size_t)bytes/3,off=0;
        while(off<count) {
            unsigned width=count-off>8192?8192:(unsigned)(count-off);
            if(nx_preview_filter_rgb888(&p,lut,in+off*3,width*3,out+off*3,width*3,width,1)) return 4;
            off+=width;
        }
    } else {
        /* Upstream variable-grid kernel, with the same upstream transfer LUTs. */
        for(long i=0;i<bytes;i+=3) {
            nx_rgb12 a={p.decode_srgb[in[i]],p.decode_srgb[in[i+1]],p.decode_srgb[in[i+2]],0},b;
            if(nx_lut_grid_host_apply(lut,n,&a,&b,1)) return 4;
            out[i]=p.encode_srgb[b.r];out[i+1]=p.encode_srgb[b.g];out[i+2]=p.encode_srgb[b.b];
        }
    }
    f=fopen(argv[4],"wb");if(!f) return 3;
    if(fwrite(out,1,bytes,f)!=(size_t)bytes||fclose(f)) return 3;
    free(lut);free(in);free(out);return 0;
}
'''

OLD_MAIN = r'''
#define main upstream_daemon_main
#include "lutd.c"
#undef main
int main(int argc,char **argv) {
    if(argc!=4) return 2;
    Lut lut;if(lut_load(argv[1],&lut)||lut_prepare(&lut))return 3;
    FILE *f=fopen(argv[2],"rb");if(!f)return 3;
    if(fseek(f,0,SEEK_END))return 3;
    long bytes=ftell(f);if(bytes<=0||bytes%3||bytes>16000000)return 3;
    rewind(f);unsigned char *data=malloc(bytes);if(!data)return 3;
    if(fread(data,1,bytes,f)!=(size_t)bytes)return 3;fclose(f);
    g_strength=100;apply_row(&lut,data,(int)(bytes/3));
    f=fopen(argv[3],"wb");if(!f)return 3;
    if(fwrite(data,1,bytes,f)!=(size_t)bytes||fclose(f))return 3;
    free(data);free(lut.t3);free(lut.t1);return 0;
}
'''


def wsl(path):
    p=Path(path).resolve().as_posix()
    return '/mnt/'+p[0].lower()+p[2:]


def run(command, *, timeout=120):
    r=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e',*command],capture_output=True,
                     text=True,encoding='utf-8',errors='replace',timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'Upstream host exit {r.returncode}: {r.stderr[-3000:]}')
    return r


def build(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'new-main.c').write_text(NEW_MAIN,encoding='utf-8',newline='\n')
    (output/'old-main.c').write_text(OLD_MAIN,encoding='utf-8',newline='\n')
    nr=REFERENCE/'new_nx300m';old=REFERENCE/'nx300-lut'
    commands=[['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-I'+wsl(nr),
               wsl(output/'new-main.c'),*(wsl(nr/f) for f in
               ['nx300m_lut18.c','nx300m_preview_rgb.c','nx300m_lut_grid_host.c']),'-lm','-o',wsl(output/'new-host')],
              ['cc','-O2','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
               '-I'+wsl(old),'-I'+wsl(ROOT/'work/reference/jpeg8-headers'),
               wsl(output/'old-main.c'),'-o',wsl(output/'old-host')]]
    for i,c in enumerate(commands):
        result=run(c)
        (output/f'build-{i}.stderr').write_text(result.stderr,encoding='utf-8')
    files={str(p.relative_to(REFERENCE)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in REFERENCE.rglob('*') if p.suffix in ('.c','.h','.py')}
    (output/'provenance.json').write_text(json.dumps({'source_root':str(REFERENCE.relative_to(ROOT)),
        'source_sha256':files,'new_backend':'actual upstream RGB888/18 and variable-grid kernels',
        'old_backend':'actual lut_load, lut_prepare and apply_row; daemon/JPEG not executed'},indent=2)+'\n',encoding='utf-8')


def decode(x):
    x=np.asarray(x)
    return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)


def encode(x):
    x=np.clip(x,0,1)
    return np.where(x<=.0031308,x*12.92,1.055*x**(1/2.4)-.055)


def table(cube, size):
    # Same conversion as upstream: linear node -> encode -> cube -> decode.
    coords=grid(size)[:,::-1]  # Standard cube R-fastest order.
    values=decode(sample_cube(cube,encode(coords)))
    words=np.zeros((len(coords),4),dtype='<u2')
    words[:,:3]=np.floor(values*4095+.5).astype('<u2')
    return words.tobytes()


def compare(build_dir, destination, source, cube, rgb):
    build_dir,destination=Path(build_dir),Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    rgb=np.asarray(rgb,dtype=np.uint8).reshape(-1,3)
    (destination/'input.rgb').write_bytes(rgb.tobytes())
    results={}
    for n in (18,65):
        native=destination/f'linear-{n}.bin';native.write_bytes(table(cube,n))
        out=destination/f'host-{n}.rgb'
        run([wsl(build_dir/'new-host'),str(n),wsl(native),wsl(destination/'input.rgb'),wsl(out)])
        results[f'host{n}']=np.frombuffer(out.read_bytes(),dtype=np.uint8).reshape(-1,3).astype(float)/255
    out=destination/'nx300-lut.rgb'
    run([wsl(build_dir/'old-host'),wsl(source),wsl(destination/'input.rgb'),wsl(out)])
    results['nx300lut']=np.frombuffer(out.read_bytes(),dtype=np.uint8).reshape(-1,3).astype(float)/255
    return results
