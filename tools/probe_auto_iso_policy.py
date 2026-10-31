"""Execute original NX300M AE ISO-policy consumers in an isolated ARM fixture.

The IQ cache and shooting mode are explicit fixtures. This confirms original
policy behavior, not a live meter, sensor gain or physical exposure.
"""
import json
from pathlib import Path
import shlex
import subprocess

from build_shutter_limit import CRT

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/controls'
GUEST = '/opt/nx300m-lab/stock-1.15'
PROBE = r'''
#include <stdio.h>
#include <string.h>
extern int max_iso_sv(void *) __asm__("_ZN9CAeInfoIF25getAeSysInfoISOMaxSettingEv");
extern int iso_sv(void *) __asm__("_ZN9CAeInfoIF22getAeSysInfoISOSettingEv");
extern void *iq_singleton __asm__("_ZN15CIQDataRepeater11m_pInstanceE");
extern int set_parameter(void *,int,int) __asm__("_ZN15CIQDataRepeater12SetParameterEii");
extern void *dlsym(void *,const char *);
int main(void) {
    unsigned int ae[128]={0}, repeater[16]={0}, parameters[128]={0};
    ae[1]=(unsigned int)repeater;repeater[2]=(unsigned int)parameters;
    int failure=0;
    int settings[]={3200,6400,6400,6400}, modes[]={0,0,3,4}, expected[]={160,176,176,128};
    for (unsigned int i=0;i<4;i++) {
        parameters[0x94/4]=(unsigned int)settings[i];
        *(short *)((unsigned char *)ae+0x6c)=(short)modes[i];
        int actual=max_iso_sv(ae);
        printf("{\"iq_max_iso\":%d,\"mode\":%d,\"ae_max_sv16\":%d,\"expected_sv16\":%d}\n",settings[i],modes[i],actual,expected[i]);
        if (actual!=expected[i]) failure++;
    }
    parameters[0x90/4]=0;
    int automatic=iso_sv(ae);
    parameters[0x90/4]=3200;
    int manual=iso_sv(ae);
    printf("{\"auto_iso_setting\":0,\"auto_sv16\":%d,\"manual_iso_setting\":3200,\"manual_sv16\":%d}\n",automatic,manual);
    if (automatic!=0 || manual!=160) failure++;
    int (*configure)(unsigned int)=dlsym((void *)0,"nx_auto_iso_max_configure");
    if (!configure) return 2;
    iq_singleton=repeater;
    parameters[0x90/4]=3200;
    for (unsigned int iso=12800;iso<=25600;iso*=2) {
        if (configure(iso)) return 3;
        if (set_parameter(repeater,37,6400)!=1 || parameters[37]!=iso) return 4;
        for (int mode=0;mode<=4;mode++) {
            *(short *)((unsigned char *)ae+0x6c)=(short)mode;
            int actual=max_iso_sv(ae),expected=mode>3?128:iso==12800?192:208;
            printf("{\"extended_max_iso\":%u,\"mode\":%d,\"ae_max_sv16\":%d,\"expected_sv16\":%d}\n",iso,mode,actual,expected);
            if (actual!=expected || parameters[0x90/4]!=3200) failure++;
        }
        if (set_parameter(repeater,37,3200)!=1 || parameters[37]!=3200) return 5;
        if (set_parameter(repeater,37,6400)!=1 || parameters[37]!=6400) return 6;
    }
    if (configure(25600) || configure(0) || parameters[37]!=6400 || configure(6400)!=-102) return 7;
    iq_singleton=0;
    printf("{\"extension_verified\":true,\"manual_iso_unchanged\":true,\"stock_choice_clears_extension\":true,\"off_restored\":true,\"physical_exposure_verified\":false}\n");
    return failure?1:0;
}
'''

BRIDGE_PROBE = r'''
#include "../../runtime/exposure_controls.c"
extern int printf(const char *,...);
extern void *iq_singleton __asm__("_ZN15CIQDataRepeater11m_pInstanceE");
static unsigned int stock=0x003b000f;
void *get_service(void) __asm__("_ZN22CDataManagementService11GetInstanceEv");
void *get_service(void) { return &stock; }
int get_data(void *,int) __asm__("_ZN22CDataManagementService11GetUserDataEi");
int get_data(void *self,int id) { (void)self;return id==0x3b?(int)stock:-1; }
int set_data(void *,int,int) __asm__("_ZN22CDataManagementService11SetUserDataEii");
/* Deliberately no notification: reproduces stock same-value 6400 writes. */
int set_data(void *self,int id,int value) { (void)self;if (id!=0x3b) return -1;stock=(unsigned int)value;return 0; }
int main(void) {
    unsigned int iq[16]={0},parameters[128]={0};iq[2]=(unsigned int)parameters;iq_singleton=iq;
    parameters[37]=6400;parameters[36]=3200;
    unsigned int requests[]={0x003b0011,0x003b000f,0x003b0010,0x003b000e};
    int expected[]={25600,6400,12800,3200};
    for (unsigned int index=0;index<4;index++) {
        void *file=fopen("/var/run/nx300m-exposure-request","w");if (!file) return 10;
        fprintf(file,"%u 4294967295 1 3b %x\n",index+1,requests[index]);fclose(file);
        exposure_poll(0);
        unsigned int id=0,before=0,actual=0;int code=-1;
        file=fopen("/var/run/nx300m-exposure-response","r");if (!file) return 11;
        int fields=fscanf(file,"%u %d %x %x",&id,&code,&before,&actual);fclose(file);
        if (fields!=4 || id!=index+1 || code || actual!=requests[index] || parameters[37]!=(unsigned int)expected[index] || parameters[36]!=3200) return 12;
        printf("{\"bridge_requested_raw\":\"0x%08x\",\"bridge_actual_raw\":\"0x%08x\",\"stock_actual_raw\":\"0x%08x\",\"iq_max_iso\":%u,\"manual_iso\":%u}\n",requests[index],actual,stock,parameters[37],parameters[36]);
    }
    iq_singleton=0;return 0;
}
'''


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'auto-iso-policy-probe.c').write_text(PROBE,encoding='utf-8')
    (OUT/'auto-iso-policy-crt0.S').write_text(CRT,encoding='utf-8')
    (OUT/'auto-iso-bridge-probe.c').write_text(BRIDGE_PROBE,encoding='utf-8')
    project='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    script=f'''set -eu
cd {shlex.quote(project)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
for probe in policy bridge; do
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--export-dynamic -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 outputs/controls/auto-iso-$probe-probe.c outputs/controls/auto-iso-policy-crt0.S \\
 -L"$rf/usr/lib" -Wl,--no-as-needed -Wl,-l:libiq-fw-slpcam.so -Wl,-l:libdiosal.so \\
 -Wl,-l:libproduction-mode.so "$rf/usr/lib/libudd.so.3.1" \\
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o outputs/controls/auto-iso-$probe-probe.arm
done
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc sh -c '
mount --make-rprivate /
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs {GUEST}/mod
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs {GUEST}/var/run
cp outputs/controls/auto-iso-policy-probe.arm {GUEST}/mod/auto-iso-policy-probe.arm
cp outputs/controls/auto-iso-bridge-probe.arm {GUEST}/mod/auto-iso-bridge-probe.arm
cp outputs/controls/exposure_controls.so {GUEST}/mod/exposure_controls.so
chmod 755 {GUEST}/mod/auto-iso-policy-probe.arm
chmod 755 {GUEST}/mod/auto-iso-bridge-probe.arm
chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -cpu cortex-a9 -L / /mod/auto-iso-bridge-probe.arm
exec chroot --userspec=65534:65534 {GUEST} /qemu-arm-static -cpu cortex-a9 -E LD_PRELOAD=/mod/exposure_controls.so -L / /mod/auto-iso-policy-probe.arm'
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',script],capture_output=True,timeout=60)
    stdout=result.stdout.decode('utf-8','replace');stderr=result.stderr.decode('utf-8','replace')
    (OUT/'auto-iso-policy-probe.stdout.log').write_text(stdout,encoding='utf-8')
    (OUT/'auto-iso-policy-probe.stderr.log').write_text(stderr,encoding='utf-8')
    report={'exit_code':result.returncode,'events':[json.loads(line) for line in stdout.splitlines() if line.startswith('{')],
            'stock_library':'unmodified NX300M 1.15 libiq-fw-slpcam.so with exposure_controls SetParameter interposition',
            'input':'explicit IQ cache and AE shooting-mode fixture',
            'shared_camera_session_changed':False,'live_ae_solver_verified':False,'physical_exposure_verified':False}
    (OUT/'auto-iso-policy-probe.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(stdout)
    if result.returncode: print(stderr)
    raise SystemExit(result.returncode)


if __name__=='__main__':main()
