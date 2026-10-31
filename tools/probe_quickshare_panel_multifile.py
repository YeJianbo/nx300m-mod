"""Isolated real ARM panel + HTTP service check with generated files only.

A loopback HTTP recorder checks that invalid argv and local error pages cause no request. A local
no-network CLI fixture records successful ordered file handoff. The main camera
namespace is never entered; this does not test phone interoperability.
"""
import argparse
import ast
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/quickshare/gallery/panel-multifile-probe'
STOCK = Path('/opt/nx300m-lab/stock-1.15')
FIRST = 'fixture one;$(must-not-run).JPG'
SECOND = 'fixture two & RAW.SRW'
PAIR_JPEG = 'format fixture & pairing.jpg'
PAIR_RAW = 'format fixture & pairing.srw'
ERROR_DRIVER_MAIN = r'''
int main(int argc,char**argv){
 if(argc!=4)return 2;
 Display*d=XOpenDisplay(NULL);if(!d)return 2;
 Window parent=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
 XMapWindow(d,parent);XSync(d,False);XSetInputFocus(d,parent,RevertToParent,CurrentTime);XFlush(d);
 FILE*ready=fopen(argv[1],"w");fputs("ready",ready);fclose(ready);
 Window w=0;for(int i=0;i<50&&!w;i++){usleep(100000);w=find(d);}if(!w)return 3;
 usleep(600000);
 Window focus;int revert;XGetInputFocus(d,&focus,&revert);int focused=focus==w;
 key(d,w,0xff54);key(d,w,0xff52);shot(d,w,argv[2]);
 XEvent e;memset(&e,0,sizeof e);
 if(!strcmp(argv[3],"button")){
  /* The single return button is centered at (380,359) on the retained surface. */
  e.xbutton.display=d;e.xbutton.window=w;e.xbutton.button=Button1;e.xbutton.same_screen=True;
  e.xbutton.x=80;e.xbutton.y=380;
  e.type=ButtonPress;XSendEvent(d,w,False,ButtonPressMask,&e);XFlush(d);usleep(150000);
  /* Nuklear can activate on press; never release into an already closed page. */
  if(find(d)==w){e.type=ButtonRelease;XSendEvent(d,w,False,ButtonReleaseMask,&e);XFlush(d);}
 }else{
  KeySym symbol=!strcmp(argv[3],"menu")?0xff67:!strcmp(argv[3],"enter")?0xff0d:0xff1b;
  e.xkey.display=d;e.xkey.window=w;e.xkey.keycode=XKeysymToKeycode(d,symbol);
  e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);XFlush(d);
 }
 for(int i=0;i<50&&find(d);i++)usleep(100000);
 XGetInputFocus(d,&focus,&revert);
 int closed=!find(d),restored=focus==parent;
 printf("{\"panel_focused\":%s,\"panel_closed\":%s,\"focus_restored\":%s}\n",focused?"true":"false",closed?"true":"false",restored?"true":"false");
 XDestroyWindow(d,parent);XCloseDisplay(d);return !focused||!closed||!restored;
}
'''
FORMAT_DRIVER_MAIN = r'''
static unsigned send_count(void){
 char path[4096];snprintf(path,sizeof path,"%s/send-count",getenv("QS_PROBE"));
 FILE*f=fopen(path,"r");unsigned count=0;if(f){fscanf(f,"%u",&count);fclose(f);}return count;
}
static void await_send(Display*d,Window w,unsigned previous){
 /* Synchronize later format changes with the actual task start. */
 for(int i=0;i<40;i++){
  if(send_count()>previous)return;
  key(d,w,0xff0d);usleep(100000);
 }
 exit(8);
}
int main(int argc,char**argv){
 if(argc!=5)return 2;
 Display*d=XOpenDisplay(NULL);if(!d)return 2;
 Window parent=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
 XMapWindow(d,parent);XSync(d,False);XSetInputFocus(d,parent,RevertToParent,CurrentTime);XFlush(d);
 FILE*ready=fopen(argv[1],"w");fputs("ready",ready);fclose(ready);
 Window w=0;for(int i=0;i<50&&!w;i++){usleep(100000);w=find(d);}if(!w)return 3;
 sleep(3);
 if(!strcmp(argv[4],"pair")){
  /* JPEG, RAW, JPEG+RAW, then the original RAW/JPEG order. */
  for(int mode=0;mode<4;mode++){
   key(d,w,0xff53);if(!mode)shot(d,w,argv[2]);
   char image[4096];snprintf(image,sizeof image,"%.*s-%d.ppm",(int)strlen(argv[2])-4,argv[2],mode);shot(d,w,image);
   unsigned previous=send_count();await_send(d,w,previous);
   if(!mode){key(d,w,0xff53);usleep(700000);key(d,w,0xff53);}
   sleep(5);
  }
 }else{
  key(d,w,0xff51);shot(d,w,argv[2]);for(int i=0;i<3;i++)key(d,w,0xff0d);sleep(1);
 }
 shot(d,w,argv[3]);key(d,w,0xff1b);
 for(int i=0;i<100&&find(d);i++)usleep(100000);
 Window focus;int revert;XGetInputFocus(d,&focus,&revert);
 int closed=!find(d),restored=focus==parent;
 printf("{\"panel_closed\":%s,\"focus_restored\":%s}\n",closed?"true":"false",restored?"true":"false");
 XDestroyWindow(d,parent);XCloseDisplay(d);return !closed||!restored;
}
'''
QUEUED_DRIVER_MAIN = r'''
static void marker_path(char *out,const char *name){snprintf(out,4096,"%s/%s",getenv("QS_PROBE"),name);}
static void mark(const char *name){char path[4096];marker_path(path,name);FILE*f=fopen(path,"w");if(!f)exit(9);fputs("ready",f);fclose(f);}
static void await_marker(const char *name){
 char path[4096];marker_path(path,name);for(int i=0;i<100;i++){if(!access(path,F_OK))return;usleep(100000);}exit(10);
}
int main(int argc,char**argv){
 if(argc!=4)return 2;
 Display*d=XOpenDisplay(NULL);if(!d)return 2;
 Window parent=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
 XMapWindow(d,parent);XSync(d,False);XSetInputFocus(d,parent,RevertToParent,CurrentTime);XFlush(d);
 FILE*ready=fopen(argv[1],"w");fputs("ready",ready);fclose(ready);
 Window w=0;for(int i=0;i<50&&!w;i++){usleep(100000);w=find(d);}if(!w)return 3;
 sleep(3);mark("delay-status");await_marker("status-held");
 unsigned previous=send_count();
 /* Exactly one Enter while the real ARM page awaits a delayed status reply. */
 key(d,w,0xff0d);key(d,w,0xff54);key(d,w,0xff51);shot(d,w,argv[2]);
 mark("release-status");
 int observed=0;for(int i=0;i<100;i++){if(send_count()>previous){observed=1;break;}usleep(100000);}
 if(!observed)return 11;
 sleep(5);shot(d,w,argv[3]);key(d,w,0xff1b);
 for(int i=0;i<100&&find(d);i++)usleep(100000);
 Window focus;int revert;XGetInputFocus(d,&focus,&revert);
 int closed=!find(d),restored=focus==parent;
 printf("{\"single_enter\":true,\"panel_closed\":%s,\"focus_restored\":%s}\n",closed?"true":"false",restored?"true":"false");
 XDestroyWindow(d,parent);XCloseDisplay(d);return !closed||!restored;
}
'''


def worker(output):
    source=ast.parse((ROOT/'tools/probe_gallery_share.py').read_text())
    HELPER=next(ast.literal_eval(node.value) for node in source.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='HELPER' for target in node.targets))
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', '/tmp'], check=True)
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    probe = Path(tempfile.mkdtemp(prefix='nx-qs-panel-'));probe.chmod(0o755)
    media = probe/'DCIM/100PHOTO';media.mkdir(parents=True)
    for name in (FIRST, SECOND, PAIR_JPEG, PAIR_RAW): (media/name).write_text('Generated panel fixture.\n')
    (media/'directory').mkdir()
    (probe/'outside.JPG').write_text('Generated outside fixture.\n')
    (media/'unreadable.JPG').write_text('Generated unreadable fixture.\n');(media/'unreadable.JPG').chmod(0)
    os.link(media/FIRST, media/'hardlink.JPG')
    (probe/'fonts').mkdir();(probe/'cache').mkdir(mode=0o777)
    shutil.copy(STOCK/'usr/share/fonts/SDIC_GP_US_20120720.ttf', probe/'fonts')
    (probe/'fonts.conf').write_text((ROOT/'mod/deployment/fonts.conf.in').read_text().replace('@MOD_ROOT@',str(probe)).replace('@FONT_CACHE@',str(probe/'cache')))
    (output/'ui-driver.c').write_text(HELPER)
    (output/'error-ui-driver.c').write_text(HELPER.split('int main(int argc,char**argv){',1)[0]+ERROR_DRIVER_MAIN)
    (output/'format-ui-driver.c').write_text(HELPER.split('int main(int argc,char**argv){',1)[0]+FORMAT_DRIVER_MAIN)
    (output/'queued-ui-driver.c').write_text(HELPER.split('int main(int argc,char**argv){',1)[0]+FORMAT_DRIVER_MAIN.split('int main(int argc,char**argv){',1)[0]+QUEUED_DRIVER_MAIN)
    subprocess.run(['gcc','-idirafter','/opt/nx300m-toolchain/usr/include',str(output/'ui-driver.c'),'/lib/x86_64-linux-gnu/libX11.so.6','-o',str(probe/'driver')],check=True)
    subprocess.run(['gcc','-idirafter','/opt/nx300m-toolchain/usr/include',str(output/'error-ui-driver.c'),'/lib/x86_64-linux-gnu/libX11.so.6','-o',str(probe/'error-driver')],check=True)
    subprocess.run(['gcc','-idirafter','/opt/nx300m-toolchain/usr/include',str(output/'format-ui-driver.c'),'/lib/x86_64-linux-gnu/libX11.so.6','-o',str(probe/'format-driver')],check=True)
    subprocess.run(['gcc','-idirafter','/opt/nx300m-toolchain/usr/include',str(output/'queued-ui-driver.c'),'/lib/x86_64-linux-gnu/libX11.so.6','-o',str(probe/'queued-driver')],check=True)
    subprocess.run(['gcc','-std=gnu99','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
                    '-idirafter','/opt/nx300m-toolchain/usr/include','-I/opt/nx300m-toolchain/usr/include/freetype2',
                    '-Iwork/reference/metol/test_server/nxui','-Iwork/reference/nx-remote-controller/externals/mongoose',
                    'tools/quickshare_panel_state_probe.c','-lm','/lib/x86_64-linux-gnu/libX11.so.6','-o',str(probe/'state-probe')],cwd=ROOT,check=True)
    state=json.loads(subprocess.check_output([str(probe/'state-probe')]))
    assert all(state.values()),state
    fixture='''#!/bin/sh
set -eu
if [ "$1" = discover ]; then
 i=0
 while [ "$i" -lt 6 ]; do
  printf '{"event":"discovered","endpoint":{"ip":"127.0.0.1","port":%s,"name":"Receiver%s","present":true}}\\n' "$((9999+i))" "$i"
  i=$((i+1))
 done
 trap 'exit 0' TERM INT
 while :; do sleep 1; done
else
 printf '%s\\n' "$@" > "$QS_PROBE/arguments.txt"
 printf '%s\\n' "$@" >> "$QS_PROBE/arguments-history.txt"
 sleep 2
 printf '%s\\n' '{"event":"transfer","message":{"state":"SendingFiles","meta":{"pin_code":"1234","sent_bytes":48,"total_bytes":48}}}'
 printf '%s\\n' '{"event":"transfer","message":{"state":"AwaitingReceiverAck","meta":{"sent_bytes":48,"total_bytes":48}}}'
 sleep 1
 printf '%s\\n' '{"event":"transfer","message":{"state":"Finished","meta":{"sent_bytes":48,"total_bytes":48,"completion_basis":"safe_disconnect_ack"}}}'
fi
'''
    (probe/'cli').write_text(fixture);(probe/'cli').chmod(0o755)
    environment=dict(os.environ,QS_PROBE=str(probe),NX_MEDIA_ROOT=str(probe/'DCIM'),DISPLAY=':113',LC_ALL='C',FONTCONFIG_FILE=str(probe/'fonts.conf'))
    calls=[]
    class Recorder(BaseHTTPRequestHandler):
        def do_GET(self): self.forward()
        def do_POST(self): self.forward()
        def log_message(self,*args): pass
        def forward(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            calls.append({'method':self.command,'path':self.path,'fields':parse_qs(body.decode(),keep_blank_values=True)})
            if self.path.endswith('/send'):
                (probe/'send-count').write_text(str(sum(call['path'].endswith('/send') for call in calls)))
            if self.path.endswith('/status') and (probe/'delay-status').exists():
                (probe/'delay-status').unlink();(probe/'status-held').write_text('ready')
                deadline=time.monotonic()+5
                while not (probe/'release-status').exists():
                    if time.monotonic()>deadline: raise TimeoutError('UI driver did not release delayed status')
                    time.sleep(.02)
            connection=http.client.HTTPConnection('127.0.0.1',8782,timeout=6)
            try:
                connection.request(self.command,self.path,body,{'Content-Type':'application/x-www-form-urlencoded'})
                response=connection.getresponse();data=response.read()
                self.send_response(response.status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            finally: connection.close()
    recorder=ThreadingHTTPServer(('127.0.0.1',8781),Recorder)
    threading.Thread(target=recorder.serve_forever,daemon=True).start()
    streams=[];processes=[]
    def launch(args,log,**kwargs):
        stream=(output/log).open('wb');streams.append(stream)
        process=subprocess.Popen(args,env=kwargs.pop('env',environment),stdout=stream,stderr=subprocess.STDOUT,**kwargs);processes.append(process);return process
    qemu=[str(STOCK/'qemu-arm-static'),'-cpu','cortex-a9','-L',str(STOCK)]
    panel=qemu+[str(ROOT/'outputs/quickshare/gallery/quickshare-panel.arm')]
    def nobody(): os.setgid(65534);os.setuid(65534)
    report={'actual_arm_panel':True,'actual_arm_remote':True,'generated_files_only':True,'main_simulator_restarted':False,
            'private_network_namespace':True,'state_regressions':state,'invalid_cases':[],'error_cases':[],'valid_cases':[],'format_cases':[],'queued_cases':[],'phone_transfer_tested':False}
    try:
        invalid=[('no-arguments',[]),('error-code-missing',['--error']),
                 ('error-code-unknown',['--error','unknown']),
                 ('error-extra-argument',['--error','selection-limit',str(media/FIRST)]),
                 ('second-missing',[str(media/FIRST),str(media/'missing.SRW')]),
                 ('second-outside',[str(media/FIRST),str(probe/'outside.JPG')]),
                 ('second-directory',[str(media/FIRST),str(media/'directory')]),
                 ('second-unreadable',[str(media/FIRST),str(media/'unreadable.JPG')]),
                 ('duplicate-hardlink',[str(media/FIRST),str(media/'hardlink.JPG')]),
                 ('too-many',[str(media/FIRST)]*17)]
        for name,paths in invalid:
            result=subprocess.run(panel+paths,env=environment,preexec_fn=nobody,capture_output=True,timeout=5)
            assert result.returncode==2 and not calls,(name,result.returncode,calls)
            report['invalid_cases'].append({'name':name,'exit_code':2,'http_requests':0})
        xvfb=launch(['Xvfb',':113','-screen','0','480x800x24','-nolisten','tcp','-ac'],'xvfb.log')
        deadline=time.monotonic()+5
        while not Path('/tmp/.X11-unix/X113').exists():
            assert time.monotonic()<deadline,'Xvfb failed to start'
            time.sleep(.05)
        for code,close in [('selection-limit','escape'),('selection-unavailable','menu'),('launch-failed','button'),('launch-failed','enter')]:
            label='error-'+code+'-'+close
            ready=probe/'ready'
            if ready.exists(): ready.unlink()
            start=len(calls)
            driver=launch([str(probe/'error-driver'),str(ready),str(output/(label+'.ppm')),close],label+'-ui.json')
            deadline=time.monotonic()+5
            while not ready.exists():
                assert time.monotonic()<deadline,'Error UI driver failed to start'
                time.sleep(.05)
            # Error pages must not require even an existing media root.
            error_environment=dict(environment,NX_MEDIA_ROOT=str(probe/'missing-media-root'))
            page=launch(panel+['--error',code],label+'-panel.log',preexec_fn=nobody,env=error_environment)
            assert driver.wait(timeout=10)==0,label+' UI driver failed'
            assert page.wait(timeout=5)==0,label+' panel failed'
            ui=json.loads((output/(label+'-ui.json')).read_text());assert all(ui.values()),ui
            assert len(calls)==start,(label,calls[start:])
            report['error_cases'].append({'code':code,'close':close,'exit_code':0,'http_requests':0,'media_root_required':False,**ui})
        service=launch(qemu+[str(ROOT/'outputs/quickshare/gallery/nx-remote-controller.arm'),'--listen','127.0.0.1:8782',
                             '--web-root',str(probe),'--media-root',str(probe/'DCIM'),'--quickshare-binary',str(probe/'cli')],'service.log')
        time.sleep(.6)
        for label,names in [('two-files',[FIRST,SECOND]),('single-file',[FIRST])]:
            ready=probe/'ready'
            if ready.exists(): ready.unlink()
            start=len(calls)
            driver=launch([str(probe/'driver'),str(ready),str(output/(label+'-devices.ppm')),str(output/(label+'-complete.ppm'))],label+'-ui.json')
            deadline=time.monotonic()+5
            while not ready.exists():
                assert time.monotonic()<deadline,'UI driver failed to start'
                time.sleep(.05)
            page=launch(panel+[str(media/name) for name in names],label+'-panel.log',preexec_fn=nobody)
            assert driver.wait(timeout=25)==0,label+' UI driver failed'
            assert page.wait(timeout=8)==0,label+' panel failed'
            ui=json.loads((output/(label+'-ui.json')).read_text());assert ui['panel_closed'] and ui['focus_restored'],ui
            forwarded=(probe/'arguments.txt').read_text().splitlines()
            expected=['send','127.0.0.1:10003','Receiver4']+[str(media/name) for name in names]
            assert forwarded==expected,(forwarded,expected)
            sends=[call for call in calls[start:] if call['path'].endswith('/send')]
            assert len(sends)==1,sends
            fields=sends[0]['fields'];assert fields['path_count']==[str(len(names))] and 'path' not in fields,fields
            for index,name in enumerate(names): assert fields['path'+str(index)]==['100PHOTO/'+name],fields
            report['valid_cases'].append({'name':label,'file_count':len(names),'send_requests':1,'ordered_argv':forwarded,'http_fields':fields,**ui})
        for mode,names in [('pair',[PAIR_RAW,PAIR_JPEG]),('missing',[PAIR_JPEG,FIRST])]:
            label='format-'+mode;ready=probe/'ready'
            if ready.exists(): ready.unlink()
            start=len(calls)
            history_start=len((probe/'arguments-history.txt').read_text().splitlines())
            driver=launch([str(probe/'format-driver'),str(ready),str(output/(label+'-selected.ppm')),str(output/(label+'-result.ppm')),mode],label+'-ui.json')
            deadline=time.monotonic()+5
            while not ready.exists():
                assert time.monotonic()<deadline,'Format UI driver failed to start'
                time.sleep(.05)
            page=launch(panel+[str(media/name) for name in names],label+'-panel.log',preexec_fn=nobody)
            assert driver.wait(timeout=45)==0,label+' UI driver failed'
            assert page.wait(timeout=8)==0,label+' panel failed'
            ui=json.loads((output/(label+'-ui.json')).read_text());assert all(ui.values()),ui
            sends=[call for call in calls[start:] if call['path'].endswith('/send')]
            expected_files=[[PAIR_JPEG],[PAIR_RAW],[PAIR_JPEG,PAIR_RAW],[PAIR_RAW,PAIR_JPEG]] if mode=='pair' else []
            assert len(sends)==len(expected_files),(label,sends)
            expected_history=[]
            for call,files in zip(sends,expected_files):
                fields=call['fields'];assert fields['path_count']==[str(len(files))],fields
                for index,name in enumerate(files): assert fields['path'+str(index)]==['100PHOTO/'+name],fields
                expected_history+=['send','127.0.0.1:9999','Receiver0']+[str(media/name) for name in files]
            history=(probe/'arguments-history.txt').read_text().splitlines()[history_start:]
            assert history==expected_history,(history,expected_history)
            report['format_cases'].append({'name':label,'send_requests':len(sends),'http_fields':[call['fields'] for call in sends],
                                           'ordered_argv_verified':True,'missing_pair_blocks_entire_batch':mode=='missing',
                                           'format_locked_during_send':mode=='pair',**ui})
        label='queued-single-enter';ready=probe/'ready'
        if ready.exists(): ready.unlink()
        start=len(calls)
        driver=launch([str(probe/'queued-driver'),str(ready),str(output/(label+'-waiting.ppm')),str(output/(label+'-complete.ppm'))],label+'-ui.json')
        deadline=time.monotonic()+5
        while not ready.exists():
            assert time.monotonic()<deadline,'Queued UI driver failed to start'
            time.sleep(.05)
        page=launch(panel+[str(media/PAIR_JPEG)],label+'-panel.log',preexec_fn=nobody)
        assert driver.wait(timeout=25)==0,label+' UI driver failed'
        assert page.wait(timeout=8)==0,label+' panel failed'
        ui=json.loads((output/(label+'-ui.json')).read_text());assert all(ui.values()),ui
        sends=[call for call in calls[start:] if call['path'].endswith('/send')]
        assert len(sends)==1,sends
        fields=sends[0]['fields']
        assert fields['path_count']==['1'] and fields['path0']==['100PHOTO/'+PAIR_JPEG] and fields['endpoint']==['127.0.0.1:9999'],fields
        assert (probe/'arguments.txt').read_text().splitlines()==['send','127.0.0.1:9999','Receiver0',str(media/PAIR_JPEG)]
        report['queued_cases'].append({'name':label,'delayed_status':True,'send_requests':1,'frozen_target_and_format':True,**ui})
        report['passed']=True
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=4)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=3)
        recorder.shutdown();recorder.server_close()
        for stream in streams: stream.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',action='store_true');parser.add_argument('--output',type=Path,default=OUT)
    args=parser.parse_args();output=args.output.resolve()
    if args.worker: worker(output);return
    output.mkdir(parents=True,exist_ok=True)
    project='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    folder='/mnt/'+output.drive[0].lower()+output.as_posix()[2:]
    result=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','unshare','--mount','--net','--ipc','--pid','--fork','--mount-proc','--kill-child',
                           'python3',project+'/tools/probe_quickshare_panel_multifile.py','--worker','--output',folder],capture_output=True,timeout=120)
    (output/'stdout.log').write_bytes(result.stdout);(output/'stderr.log').write_bytes(result.stderr)
    print(result.stdout.decode('utf-8','replace'))
    if result.returncode: raise RuntimeError(f'Panel multi-file probe exit {result.returncode}: '+result.stderr.decode('utf-8','replace'))
    from PIL import Image
    for file in output.glob('*.ppm'):
        with Image.open(file) as picture: picture.transpose(Image.Transpose.ROTATE_90).save(file.with_suffix('.png'))


if __name__=='__main__': main()
