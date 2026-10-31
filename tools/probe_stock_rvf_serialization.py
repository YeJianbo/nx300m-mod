"""Call only stock RVF URL and SOAP formatters; no wireless application or session."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/nxconnect/stock-rvf/serialization'
GUEST = '/opt/nx300m-lab/stock-1.15'

C_SOURCE = r'''
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern char *dlerror(void);
extern int printf(const char *, ...);
extern int fflush(void *);
extern void exit(int);
static void *symbol(void *h, const char *n) {
    void *p = dlsym(h, n);
    if (!p) { printf("MISSING %s: %s\n", n, dlerror()); exit(2); }
    return p;
}
int main(void) {
    void *api = dlopen("/usr/lib/libdi-network-dlna-api.so", 257);
    if (!api) { printf("DLOPEN_API_FAILED %s\n", dlerror()); return 1; }
    void *h = dlopen("/usr/lib/libdi-network-dlna-dmsui.so", 257);
    if (!h) { printf("DLOPEN_FAILED %s\n", dlerror()); return 1; }
    void *r = dlopen("/usr/lib/libdi-network-dlna-rvf.so", 257);
    if (!r) { printf("DLOPEN_RVF_FAILED %s\n", dlerror()); return 1; }
    void (*string)(void *,const char *) = symbol(h,"_ZN12DMSAppStringC1EPKc");
    const char *(*read_string)(void *) = symbol(h,"_ZN12DMSAppString10ReadStringEv");
    void (*list_ctor)(void *) = symbol(h,"_ZN10DMSCDSListC1Ev");
    void (*list_add)(void *,void *) = symbol(h,"_ZN10DMSCDSList7AddItemEPv");
    void (*message_ctor)(void *) = symbol(h,"_ZN14SOAPAppMessageC1Ev");
    const char *(*message_body)(void *) = symbol(h,"_ZN14SOAPAppMessage12ReadSOAPBodyEv");
    void *(*generator)(void) = symbol(h,"_ZN16SOAPAppGenerator11GetInstanceEv");
    int (*serialize)(void *,void *,void *,void *,void *) = symbol(h,"_ZN16SOAPAppGenerator16GenerateSOAPBodyEP12DMSAppStringS1_P10DMSCDSListP14SOAPAppMessage");
    void (*set_port)(int) = symbol(h,"_Z26DMSAppSetHTTPStreamingPorti");
    void *(*url)(void *,void *) = symbol(r,"_ZN24UPnPCDSRVFGetInformation27get_streamQualityUrl_stringEP12DMSAppString");
    unsigned int ip[16]={0}, service[16]={0}, action[16]={0}, name[16]={0};
    unsigned int list[16]={0}, message[64]={0}, unused_this[64]={0};
    string(ip,"192.0.2.10");
    string(service,"urn:schemas-upnp-org:service:ContentDirectory:1");
    string(action,"GetInfomation");
    string(name,"StreamUrl");
    set_port(7677);
    void *fragment = url(unused_this,ip);
    printf("URL_BEGIN\n%s\nURL_END\n",read_string(fragment));
    fflush(0);
    struct { void *name; void *value; } argument = {name,fragment};
    list_ctor(list);
    list_add(list,&argument);
    message_ctor(message);
    int result=serialize(generator(),service,action,list,message);
    printf("SERIALIZER_RESULT=%d\nSOAP_BEGIN\n%s\nSOAP_END\n",result,message_body(message));
    fflush(0);
    return result;
}
'''


def wsl(path):
    path = path.resolve()
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'probe.c').write_text(C_SOURCE, encoding='utf-8')
    base, output = wsl(ROOT), wsl(OUT)
    compile_script = f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={shlex.quote(GUEST)}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 {shlex.quote(output + '/probe.c')} work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o {shlex.quote(output + '/probe.arm')}
'''
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', compile_script],
                            capture_output=True, timeout=30)
    (OUT / 'compile.stdout.log').write_bytes(result.stdout)
    (OUT / 'compile.stderr.log').write_bytes(result.stderr)
    if result.returncode:
        print('Compile exit', result.returncode)
        return result.returncode
    inner = f'''set -eu
ulimit -c 0
mount --make-rprivate /
mount --bind {shlex.quote(GUEST)} {shlex.quote(GUEST)}
mount -o remount,bind,ro {shlex.quote(GUEST)}
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs {shlex.quote(GUEST + '/tmp')}
cp {shlex.quote(output + '/probe.arm')} {shlex.quote(GUEST + '/tmp/rvf-format-probe.arm')}
chmod 755 {shlex.quote(GUEST + '/tmp/rvf-format-probe.arm')}
exec chroot --userspec=65534:65534 {shlex.quote(GUEST)} /qemu-arm-static -cpu cortex-a9 -strace -L / /tmp/rvf-format-probe.arm
'''
    command = ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'timeout', '--kill-after=2s', '15s',
               'unshare', '--mount', '--net', '--ipc', '--pid', '--fork', '--kill-child', 'sh', '-c', inner]
    (OUT / 'command.json').write_text(json.dumps(command, indent=2), encoding='utf-8')
    result = subprocess.run(command, capture_output=True, timeout=22)
    (OUT / 'run.stdout.log').write_bytes(result.stdout)
    (OUT / 'run.stderr.log').write_bytes(result.stderr)
    stdout = result.stdout.decode('utf-8', 'replace')
    report = {'compile_exit': 0, 'run_exit': result.returncode,
              'stock_functions_requested': ['DMSAppSetHTTPStreamingPort', 'get_streamQualityUrl_string', 'SOAPAppGenerator::GenerateSOAPBody'],
              'wireless_stack_started': False, 'camera_session_started': False,
              'fixture_ip': '192.0.2.10', 'fixture_streaming_port': 7677}
    if result.returncode == 0 and 'SOAP_BEGIN\n' in stdout:
        report['stock_functions_completed'] = True
        soap = stdout.split('SOAP_BEGIN\n', 1)[1].split('\nSOAP_END', 1)[0]
        (OUT / 'stock-generated-response-body.xml').write_text(soap, encoding='utf-8')
        document = ET.fromstring(soap)
        report['parsed_quality_high_url'] = document.findtext('.//QualityHighUrl')
        report['parsed_quality_low_url'] = document.findtext('.//QualityLowUrl')
        report['stream_url_form'] = 'XML child elements' if document.find('.//QualityHighUrl') is not None else 'text'
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(stdout)
    print(json.dumps(report))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
