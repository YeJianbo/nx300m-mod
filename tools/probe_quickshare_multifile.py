"""Exercise actual ARM remote multi-file HTTP handling with generated files only.

A new mount/network/IPC/PID namespace runs Xvfb, the built remote, and a no-network
ARM argv recorder instead of Quick Share. No main camera is entered or restarted.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/quickshare/gallery/multifile-probe'
GUEST = Path('/opt/nx300m-lab/stock-1.15')
FIXTURE = r'''
#include <stdio.h>
#include <string.h>
int main(int argc,char **argv) {
    FILE *calls=fopen("/var/run/qs-fixture-invocations","a");
    FILE *record=fopen("/var/run/qs-fixture-argv","wb");
    if (!calls || !record) return 2;
    fputs("called\n",calls);fclose(calls);
    for(int i=0;i<argc;i++) if(fwrite(argv[i],1,strlen(argv[i])+1,record)!=strlen(argv[i])+1) return 3;
    if(fclose(record)) return 4;
    puts("{\"event\":\"Finished\",\"fixture\":true,\"network_used\":false}");
    return 0;
}
'''


def request(method,path,fields=None):
    body=urlencode(fields or {}) if fields is not None else None
    connection=http.client.HTTPConnection('127.0.0.1',8781,timeout=5)
    connection.request(method,path,body,{'Content-Type':'application/x-www-form-urlencoded'})
    response=connection.getresponse();data=response.read();status=response.status;connection.close()
    return status,json.loads(data)


def worker(output):
    def command(*args): subprocess.run(args,check=True,capture_output=True)
    command('mount','--make-rprivate','/')
    command('ip','link','set','lo','up')
    for path in (GUEST/'tmp',GUEST/'var/run',Path('/tmp')):
        command('mount','-t','tmpfs','-o','mode=1777,nosuid,nodev','tmpfs',str(path))
    (Path('/tmp')/'.X11-unix').mkdir(mode=0o1777)
    (GUEST/'tmp/.X11-unix').mkdir(mode=0o1777)
    command('mount','--bind','/tmp/.X11-unix',str(GUEST/'tmp/.X11-unix'))
    media=GUEST/'tmp/qs-media';media.mkdir(mode=0o755)
    names=['fixture-one.JPG','fixture-two.SRW']+[f'fixture-{i}.dat' for i in range(2,16)]
    for i,name in enumerate(names): (media/name).write_bytes((f'generated fixture {i}\n').encode())
    (media/'directory').mkdir()
    (GUEST/'tmp/outside.dat').write_bytes(b'outside generated fixture\n')
    (media/'unreadable.dat').write_bytes(b'unreadable generated fixture\n')
    (media/'unreadable.dat').chmod(0)
    os.link(media/names[0],media/'hardlink.JPG')
    shutil.copyfile(ROOT/'outputs/quickshare/gallery/nx-remote-controller.arm',GUEST/'tmp/qs-remote.arm')
    shutil.copyfile(output/'argv-recorder.arm',GUEST/'tmp/qs-fixture.arm')
    (GUEST/'tmp/qs-remote.arm').chmod(0o755);(GUEST/'tmp/qs-fixture.arm').chmod(0o755)
    logs=[(output/'xvfb.log').open('wb'),(output/'server.stdout.log').open('wb'),(output/'server.stderr.log').open('wb')]
    xvfb=subprocess.Popen(['Xvfb',':99','-screen','0','480x800x24','-nolisten','tcp','-ac'],stdout=logs[0],stderr=subprocess.STDOUT)
    server=None;report={'generated_files_only':True,'actual_arm_remote':True,'fixture_cli_has_network_code':False,
                        'main_simulator_restarted':False,'invalid_cases':[],'valid_cases':[]}
    try:
        deadline=time.monotonic()+5
        while not Path('/tmp/.X11-unix/X99').exists() and time.monotonic()<deadline: time.sleep(.05)
        environment=os.environ.copy();environment['DISPLAY']=':99'
        server=subprocess.Popen(['chroot','--userspec=65534:65534',str(GUEST),'/qemu-arm-static','-cpu','cortex-a9','-L','/',
                                  '/tmp/qs-remote.arm','--listen','127.0.0.1:8781','--media-root','/tmp/qs-media',
                                  '--web-root','/tmp','--quickshare-binary','/tmp/qs-fixture.arm',
                                  '--quickshare-runner','/qemu-arm-static'],env=environment,stdout=logs[1],stderr=logs[2])
        deadline=time.monotonic()+12
        while True:
            try:
                status,_=request('GET','/api/v1/quickshare/status')
                if status==200: break
            except (OSError,ValueError,http.client.HTTPException): pass
            if time.monotonic()>deadline: raise RuntimeError('Isolated ARM remote failed to start')
            time.sleep(.1)
        common={'endpoint':'127.0.0.1:9','name':'fixture receiver;$(must-not-run)','client_token':'fixture-token'}
        pair={'path_count':'2','path0':names[0],'path1':names[1]}
        invalid=[('second-outside',dict(pair,path1='../outside.dat')),
                 ('second-missing',dict(pair,path1='missing.SRW')),
                 ('second-directory',dict(pair,path1='directory')),
                 ('second-unreadable',dict(pair,path1='unreadable.dat')),
                 ('second-nul',dict(pair,path1=names[1]+'\0ignored')),
                 ('duplicate-file',dict(pair,path1='./'+names[0])),
                 ('duplicate-hardlink',dict(pair,path1='hardlink.JPG')),
                 ('mixed-single-multiple',dict(pair,path=names[0])),
                 ('missing-index',{'path_count':'2','path0':names[0]}),
                 ('extra-index',dict(pair,path2=names[0])),
                 ('over-count',dict(pair,path_count='17')),
                 ('duplicate-field',list(pair.items())+[('path1',names[1])])]
        calls=GUEST/'var/run/qs-fixture-invocations'
        for name,fields in invalid:
            fields=list(common.items())+list(fields.items() if isinstance(fields,dict) else fields)
            status,body=request('POST','/api/v1/quickshare/send',fields)
            assert status==400,(name,status,body)
            assert not calls.exists(),f'{name} started the fixture CLI'
            report['invalid_cases'].append({'name':name,'status':status,'process_started':False})
        for label,fields,expected in [('two-files',pair,names[:2]),
                                      ('legacy-single',{'path':names[0]},names[:1]),
                                      ('maximum16',dict({'path_count':'16'},**{f'path{i}':n for i,n in enumerate(names)}),names)]:
            status,body=request('POST','/api/v1/quickshare/send',dict(common,**fields))
            assert status==202 and body['file_count']==len(expected),(label,status,body)
            assert body['client_token']==common['client_token']
            assert body['total_size']==sum((media/name).stat().st_size for name in expected)
            deadline=time.monotonic()+8
            while True:
                _,finished=request('GET','/api/v1/quickshare/status')
                if not finished['running']: break
                if time.monotonic()>deadline: raise RuntimeError('Fixture CLI did not finish')
                time.sleep(.05)
            assert finished['exit_code']==0,finished
            argv=(GUEST/'var/run/qs-fixture-argv').read_bytes().split(b'\0')[:-1]
            argv=[argument.decode('utf-8') for argument in argv]
            wanted=['/tmp/qs-fixture.arm','send',common['endpoint'],common['name']]+['/tmp/qs-media/'+name for name in expected]
            assert argv==wanted,(argv,wanted)
            report['valid_cases'].append({'name':label,'status':status,'file_count':body['file_count'],
                                           'total_size':body['total_size'],'argv':argv,'task_id':body['task_id'],
                                           'client_token':body['client_token'],'fixture_exit_code':finished['exit_code']})
        assert len(calls.read_text().splitlines())==3
        report['passed']=True
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:
        for process in (server,xvfb):
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=3)
        for stream in logs: stream.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--output',type=Path,default=OUT)
    args=parser.parse_args();output=args.output.resolve()
    if args.worker: worker(output);return
    output.mkdir(parents=True,exist_ok=True)
    (output/'argv-recorder.c').write_text(FIXTURE,encoding='utf-8')
    base='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    target='/mnt/'+output.drive[0].lower()+output.as_posix()[2:]
    script=f'''set -eu
cd {shlex.quote(base)}
tc=/opt/nx300m-toolchain/usr
rf={GUEST.as_posix()}
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=c99 -O0 -Wall -Wextra -Werror -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(target+'/argv-recorder.c')} work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(target+'/argv-recorder.arm')}
unset LD_LIBRARY_PATH
exec unshare --mount --net --ipc --pid --fork --kill-child \\
 python3 {shlex.quote(base+'/tools/probe_quickshare_multifile.py')} --worker --output {shlex.quote(target)}
'''
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-c',script],
                          cwd=ROOT,capture_output=True,timeout=60)
    (output/'stdout.log').write_bytes(result.stdout);(output/'stderr.log').write_bytes(result.stderr)
    print(result.stdout.decode('utf-8','replace'))
    if result.returncode:
        raise RuntimeError(f'Multi-file isolated probe exit{result.returncode}: {result.stderr.decode("utf-8","replace")}')


if __name__=='__main__': main()
