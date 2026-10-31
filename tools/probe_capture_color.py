"""Run an independent stock capture attempt and collect color/task observations.

Uses the established lab GPIO/device/Bayer replay inputs. Never enters or stops
the main viewer namespace, and never attaches its SD card. No pixel equivalence
or completed photograph follows from observing original Gamma/CCM calls. The
explicit JPEG diagnostic option encodes unverified buffer snapshots on its own
laboratory card; it does not establish a valid sensor/ISP photograph.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT/'outputs/firmware-runtime/capture-color'
GUEST = '/opt/nx300m-lab/stock-1.15'


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def linux_path(path):
    return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]


def identity(pid):
    proc = Path('/proc')/str(pid)
    try:
        stat = (proc/'stat').read_text()
        return {'pid':pid,'start_ticks':int(stat[stat.rfind(')')+2:].split()[19]),
                'mount_namespace':str((proc/'ns/mnt').readlink()),
                'cmdline':(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode('utf-8','replace')}
    except (FileNotFoundError,ProcessLookupError): return None


def worker(output, protected_pid, startup_seconds, capture_seconds):
    # This worker runs under WSL, outside both camera PID namespaces. Every
    # namespace operation explicitly targets this output directory's session.
    session = json.loads((output/'session.json').read_text(encoding='utf-8'))
    lut_request = json.loads((output/'launch.json').read_text(encoding='utf-8')).get('lut_request')
    pid = int(session['namespace_pid'])
    if pid == protected_pid or pid == 10799 or pid <= 1:
        raise RuntimeError('Refusing to operate on the main/protected namespace')
    original_identity = identity(pid)
    protected_before = identity(protected_pid) if protected_pid else None
    if not original_identity or GUEST not in original_identity['cmdline']:
        raise RuntimeError('Independent namespace identity is not the stock lab supervisor')
    if protected_before and original_identity['mount_namespace'] == protected_before['mount_namespace']:
        raise RuntimeError('Independent capture shares the main mount namespace')
    root = Path('/proc')/str(pid)/'root'/GUEST.lstrip('/')
    runtime = root/'var/run'
    environment = os.environ.copy()
    environment['NX300M_SESSION_FILE'] = str(output/'session.json')
    os.environ['NX300M_SESSION_FILE'] = str(output/'session.json')
    report = {'started_utc':datetime.now(timezone.utc).isoformat(),
              'namespace':original_identity,'protected_main_before':protected_before,
              'scope':'original camera capture attempt with laboratory input/transport backends',
              'synthetic_node_probe':False,'recorded_bayer_input':True,
              'active_physical_sensor':False,'color_style_enabled':False,'custom_lut_enabled':bool(lut_request),
              'lut_request':lut_request,
              'inputs':[],'snapshots':[],'namespace_stopped':False,'new_capture_jpeg_verified':False,
              'image_verified':False}
    observed = set()
    capture_observed = set()
    report['capture_observer_enabled'] = bool(session.get('capture_observer'))
    report['jpeg_task_trace_enabled'] = bool(session.get('jpeg_task_trace'))
    report['jpeg_cpu_diagnostic_enabled'] = bool(session.get('jpeg_cpu_diagnostic'))
    report['source_pixels_unverified'] = report['jpeg_cpu_diagnostic_enabled']
    report['diagnostic_sample_source'] = session.get('diagnostic_sample_source')
    report['software_isp_source'] = session.get('software_isp_source')
    report['capture_events'] = []
    snapshot_root = output/'observer'
    snapshot_root.mkdir(exist_ok=True)
    before_media = set()
    progress_path = output/'progress.json'
    replay_process = None
    replay_logs = []

    def collect(phase):
        count = 0
        lut_status = runtime/'nx300m-lut-status.json'
        if lut_status.is_file():
            try: report['lut_status'] = json.loads(lut_status.read_text())
            except (OSError, ValueError): pass
        for source in runtime.glob('nx300m-geometry-*'):
            if source.suffix not in ('.txt', '.bin') or not source.is_file(): continue
            destination = output/'geometry'/source.name
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(source.read_bytes())
        for directory in runtime.glob('nx300m-capture-observer-*'):
            if not directory.is_dir(): continue
            for source in directory.glob('*.json'):
                try: data = source.read_bytes(); record = json.loads(data)
                except (OSError,ValueError): continue
                key = (directory.name,source.name)
                if key in capture_observed: continue
                capture_observed.add(key)
                destination = output/'capture-observer'/directory.name/source.name
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(data)
                report['capture_events'].append({'file':str(destination.relative_to(output)),
                    'phase_collected':phase,'guest_pid':record.get('guest_pid'),
                    'sequence':record.get('sequence'),'kind':record.get('kind')})
        for directory in runtime.glob('nx300m-isp-color-*'):
            if not directory.is_dir(): continue
            for source in directory.glob('*.json'):
                try: data = source.read_bytes(); record = json.loads(data)
                except (OSError,ValueError): continue
                key = (record.get('guest_pid'),record.get('sequence'),record.get('kind'))
                if key in observed: continue
                observed.add(key)
                destination = snapshot_root/f"{key[0]}-{key[1]}-{key[2]}.json"
                destination.write_bytes(data)
                report['snapshots'].append({'file':str(destination.relative_to(output)),
                                             'phase_collected':phase,'guest_pid':key[0],
                                             'sequence':key[1],'kind':key[2],
                                             'source_directory':str(directory).split('/var/run/')[-1],
                                             'caller_library':record.get('caller_library'),
                                             'caller_relative_offset':record.get('caller_relative_offset'),
                                             'next_provider':record.get('next_provider')})
                count += 1
        write_json(progress_path,{'phase':phase,'namespace_pid':pid,
                                  'snapshots':len(observed),'updated_utc':datetime.now(timezone.utc).isoformat()})
        return count

    def input_event(*arguments):
        command = ['nsenter','-t',str(pid),'--mount','--net','python3',
                   str(ROOT/'tools/input_stock.py'),'--screen',session['raw_screen'],*arguments]
        result = subprocess.run(command,env=environment,capture_output=True,timeout=8)
        report['inputs'].append({'action':list(arguments),'exit_code':result.returncode,
                                  'stdout':result.stdout.decode('utf-8','replace').strip(),
                                  'stderr':result.stderr.decode('utf-8','replace').strip()})
        return result.returncode == 0

    def camera_processes():
        processes = []
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal(): continue
            observed_identity = identity(int(path.name))
            if observed_identity and observed_identity['mount_namespace'] == original_identity['mount_namespace']:
                if observed_identity['cmdline'].startswith('/qemu-arm-static ') and '/usr/bin/di-camera-app-nx300' in observed_identity['cmdline']:
                    processes.append(observed_identity)
        return processes

    def thread_snapshot(label):
        records = []
        for process in camera_processes():
            proc = Path('/proc')/str(process['pid'])
            entry = {'identity':process,'threads':[]}
            try:
                (output/f'{label}-{process["pid"]}.maps').write_text((proc/'maps').read_text())
                for task in (proc/'task').iterdir():
                    thread = {'tid':int(task.name)}
                    for field in ('stat','wchan','syscall','stack'):
                        try: thread[field] = (task/field).read_text().strip()
                        except OSError as exc: thread[field+'_error'] = str(exc)
                    entry['threads'].append(thread)
            except OSError as exc: entry['error'] = str(exc)
            records.append(entry)
        write_json(output/(label+'.json'),records)

    try:
        print(f'Independent namespace={pid}; waiting for stock startup',flush=True)
        deadline = time.monotonic()+startup_seconds
        while time.monotonic() < deadline:
            collect('startup')
            time.sleep(.5)
        layout = json.loads((output/'pipeline-layout.json').read_text(encoding='utf-8'))
        replay_command = ['nsenter','-t',str(pid),'--mount','--net','python3',
                          str(ROOT/'tools/replay_ipcm.py'),'--submit',
                          str(ROOT/'outputs/firmware-runtime/ipcm-replay-input.json'),
                          '--layout',json.dumps(layout),'--frames','0','--fps','5']
        replay_logs = [(output/'delayed-replay.stdout.log').open('wb'),
                       (output/'delayed-replay.stderr.log').open('wb')]
        replay_process = subprocess.Popen(replay_command,env=environment,
                                           stdout=replay_logs[0],stderr=replay_logs[1])
        report['replay_worker_pid'] = replay_process.pid
        print('Delayed recorded IPCM replay started after bounded startup observation',flush=True)
        time.sleep(3)
        from pipeline_state import read_state
        def pipeline_snapshot(label):
            try:
                state = read_state(layout)
                write_json(output/(label+'.json'),state)
                return state
            except (OSError,ValueError,RuntimeError,StopIteration) as exc:
                report.setdefault('pipeline_read_errors',[]).append({'phase':label,'error':str(exc)})
                return {}
        pipeline_snapshot('pipeline-before-input')
        thread_snapshot('threads-before-input')
        report['camera_processes_before_input'] = camera_processes()
        report['preload_process_args'] = [item['cmdline'] for item in report['camera_processes_before_input']]
        # Report the actual loaded process maps, rather than relying solely on
        # the requested CLI flags to establish absence of color-changing shims.
        loaded = set()
        for process in report['camera_processes_before_input']:
            try:
                for line in (Path('/proc')/str(process['pid'])/'maps').read_text().splitlines():
                    if '/tmp/' in line and '.so' in line: loaded.add(line.split()[-1])
            except OSError: pass
        report['loaded_temporary_shims'] = sorted(loaded)
        forbidden_names = ('color-style','chroma','contrast_style','source_rgb') + (() if lut_request else ('lut_port',))
        forbidden = [path for path in loaded if any(name in path for name in forbidden_names)]
        if forbidden: raise RuntimeError(f'Unexpected color-changing preload: {forbidden}')
        if lut_request:
            if GUEST+'/tmp/lut_port.so' not in loaded:
                raise RuntimeError('Requested LUT adapter is not present in the independent camera maps')
            # Only this session's private /tmp and /run are written. Reuse the
            # native engine, including its hook and deployed-library checks.
            engine = root/'tmp/lut-capture-engine.arm'
            shutil.copyfile(ROOT/'outputs/metol/lut_engine.arm', engine)
            engine.chmod(0o755)
            selected = subprocess.run(['nsenter','-t',str(pid),'--mount','--net','--ipc',
                'chroot','--userspec=65534:65534',GUEST,'/qemu-arm-static','-L','/',
                '/tmp/lut-capture-engine.arm','apply',lut_request['id'],str(lut_request['strength'])],
                capture_output=True,timeout=10)
            report['lut_stage'] = {'exit_code':selected.returncode,
                'stdout':selected.stdout.decode('utf-8','replace'), 'stderr':selected.stderr.decode('utf-8','replace')}
            if selected.returncode:
                raise RuntimeError('Native LUT selection failed: '+report['lut_stage']['stdout']+report['lut_stage']['stderr'])
        for path in (root/'mnt/mmc/DCIM').rglob('*'):
            if path.is_file(): before_media.add(str(path.relative_to(root/'mnt/mmc')))
        if not input_event('shutter','full'):
            raise RuntimeError('Independent stock shutter input failed')
        print('Stock shutter input delivered; collecting color callbacks and explicit PWM edges',flush=True)
        deadline = time.monotonic()+capture_seconds
        capture_started = time.monotonic()
        retries = 0
        pulse_count = 0
        next_thread_snapshot = time.monotonic()+10
        fifo_fd = None
        while time.monotonic() < deadline:
            if time.monotonic() >= next_thread_snapshot:
                thread_snapshot(f'threads-capture-{int(time.monotonic()-capture_started)}')
                next_thread_snapshot = time.monotonic()+10
            fifo = runtime/'nx300m-pwm-events'
            if fifo_fd is None and fifo.exists():
                try: fifo_fd = os.open(fifo,os.O_WRONLY|os.O_NONBLOCK)
                except OSError: pass
            if fifo_fd is not None:
                try:
                    # Existing lab protocol: external source3 high then falling
                    # edge. This supplies input levels, not completion callbacks.
                    os.write(fifo_fd,b'7\n6\n')
                    pulse_count += 1
                except OSError:
                    os.close(fifo_fd); fifo_fd = None
            collect('after-shutter-input')
            if session.get('software_isp_source') and any(event['kind']=='write-chunks' for event in report['capture_events']):
                completed=[]
                for photo in (root/'mnt/mmc/DCIM').rglob('*'):
                    if not photo.is_file() or photo.suffix.lower() not in ('.jpg','.jpeg'): continue
                    relative=str(photo.relative_to(root/'mnt/mmc'))
                    if relative in before_media: continue
                    with photo.open('rb') as stream:
                        if stream.read(2)!=b'\xff\xd8': continue
                        stream.seek(-2,2)
                        if stream.read()!=b'\xff\xd9': continue
                    destination=output/'media'/photo.relative_to(root/'mnt/mmc')
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(photo,destination)
                    completed.append(str(destination.relative_to(output)))
                if completed:
                    report['saved_software_isp_jpegs']=completed
                    report['stop_reason']='original WriteChunks returned and complete new JPEG copied for independent decode'
                    break
            if retries < 2 and time.monotonic()-capture_started >= 15*(retries+1):
                retries += 1
                state = pipeline_snapshot(f'pipeline-retry-{retries}')
                if state.get('capture',{}).get('state') == 'idle':
                    input_event('shutter','full')
            trace = (output/session['stderr_log']).read_text(encoding='utf-8',errors='replace')
            if 'DscJfif.cpp' in trace and 'mybuf.size > 0' in trace:
                report['stop_reason'] = 'original JPEG assertion observed'
                break
            if pulse_count and not camera_processes():
                report['stop_reason'] = 'original camera processes exited'
                break
            time.sleep(.25)
        if fifo_fd is not None: os.close(fifo_fd)
        report['pwm_external_source3_falling_edges'] = pulse_count
        report.setdefault('stop_reason','bounded observation duration reached')
        collect('final-before-stop')
        pipeline_snapshot('pipeline-before-stop')
        report['camera_processes_before_stop'] = camera_processes()
        media = [str(path.relative_to(root/'mnt/mmc')) for path in (root/'mnt/mmc/DCIM').rglob('*')
                 if path.is_file()]
        report['new_media_files'] = [name for name in media if name not in before_media]
        for name in ('nx300m-sdcard.json','nx300m-shutter-motion.bin','nx300m-pwm-state.bin',
                     'nx300m-pp-bayer-state.bin','nx300m-ipcs-state.bin',
                     'nx300m-pp-memory-task.bin','nx300m-pp-cs-at-memory.bin'):
            source = runtime/name
            if source.is_file(): (output/name).write_bytes(source.read_bytes())
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        collect('error-before-stop')
    finally:
        trace_path = output/session['stderr_log']
        tasks, callers, diagnostics, software_isp = [], [], [], []
        if trace_path.is_file():
            for line in trace_path.read_text(encoding='utf-8',errors='replace').splitlines():
                prefix = '[JPEG TASK] '
                if line.startswith(prefix):
                    try: tasks.append(json.loads(line[len(prefix):]))
                    except ValueError: pass
                prefix = '[JPEG CALLER] '
                if line.startswith(prefix):
                    try: callers.append(json.loads(line[len(prefix):]))
                    except ValueError: pass
                prefix = '[JPEG DIAGNOSTIC] '
                if line.startswith(prefix):
                    try: diagnostics.append(json.loads(line[len(prefix):]))
                    except ValueError: pass
                prefix = '[SOFTWARE ISP] '
                if line.startswith(prefix):
                    try: software_isp.append(json.loads(line[len(prefix):]))
                    except ValueError: pass
        report['jpeg_tasks'] = tasks
        report['jpeg_callers'] = callers
        report['jpeg_diagnostics'] = diagnostics
        report['software_isp_tasks'] = software_isp
        if report['jpeg_task_trace_enabled']:
            write_json(output/'jpeg-tasks.json',{'tasks':tasks,'callers':callers,'diagnostics':diagnostics,'software_isp_tasks':software_isp,
                'original_encoder_preserved':not (report['jpeg_cpu_diagnostic_enabled'] or report['software_isp_source']),
                'software_isp_source':report['software_isp_source'],
                'source_pixels_unverified':report['jpeg_cpu_diagnostic_enabled'],
                'diagnostic_sample_source':report['diagnostic_sample_source'],
                'producer_authorization_checked':False,'new_capture_jpeg_verified':False})
        if replay_process is not None and replay_process.poll() is None:
            replay_process.terminate()
            try: replay_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                replay_process.kill(); replay_process.wait(timeout=3)
        if replay_process is not None: report['replay_worker_exit_code'] = replay_process.returncode
        for stream in replay_logs: stream.close()
        current = identity(pid)
        if current and current['start_ticks'] == original_identity['start_ticks']:
            if pid in (10799,protected_pid): raise RuntimeError('Protected namespace cannot be stopped')
            os.kill(pid,signal.SIGKILL)
            deadline = time.monotonic()+10
            while time.monotonic() < deadline and identity(pid): time.sleep(.1)
        report['namespace_stopped'] = identity(pid) is None
        report['protected_main_after'] = identity(protected_pid) if protected_pid else None
        report['protected_main_identity_unchanged'] = report['protected_main_after'] == protected_before
        report['completed_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(output/'capture.json',report)
    print(json.dumps({'namespace_pid':pid,'snapshots':len(report['snapshots']),
                      'kinds':[item['kind'] for item in report['snapshots']],
                      'capture_observer_enabled':report['capture_observer_enabled'],
                      'capture_event_kinds':[item['kind'] for item in report['capture_events']],
                      'stop_reason':report.get('stop_reason'),'error':report.get('error'),
                      'namespace_stopped':report['namespace_stopped'],
                      'protected_main_identity_unchanged':report['protected_main_identity_unchanged']},indent=2),flush=True)
    return 1 if report.get('error') else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--card-image',default='/opt/nx300m-lab/media/nx300m-capture-color.img')
    parser.add_argument('--startup-seconds',type=int,default=25)
    parser.add_argument('--capture-seconds',type=int,default=90)
    parser.add_argument('--capture-observer',action='store_true',help='Include passive stock file-write/backend observation')
    parser.add_argument('--capture-geometry',action='store_true',help='Collect pool delivery, split and second PP source snapshots')
    parser.add_argument('--jpeg-task-trace',action='store_true',help='Passively record original JPEG encode task and requested memory ranges')
    parser.add_argument('--jpeg-cpu-diagnostic',action='store_true',help='Encode unknown producer byte snapshots in the independent laboratory session only')
    parser.add_argument('--diagnostic-sample-dir',help='Absolute guest directory of external complete Y/CbCr sample planes')
    parser.add_argument('--software-isp-source',type=Path,help='Source metadata for real-Bayer software ISP model')
    parser.add_argument('--lut-look',help='Select this deployed LUT through the native engine in the private software ISP session')
    parser.add_argument('--lut-strength',type=int,default=100,help='LUT strength 0..100; only with --lut-look')
    parser.add_argument('--strace',action='store_true',help='Enable guest syscall tracing for a bounded startup failure diagnosis')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--protected-pid',type=int,default=10799,help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0 <= args.lut_strength <= 100:
        parser.error('--lut-strength must be 0..100')
    if args.lut_look and not args.software_isp_source:
        parser.error('--lut-look requires the explicit software ISP source')
    if args.lut_look:
        from look_library import LookLibrary
        LookLibrary().get(args.lut_look)
    if args.software_isp_source:
        if args.jpeg_cpu_diagnostic: parser.error('Select software ISP or external diagnostic snapshots')
        args.jpeg_task_trace=True
        if args.card_image=='/opt/nx300m-lab/media/nx300m-capture-color.img':
            args.card_image='/opt/nx300m-lab/media/nx300m-software-isp.img'
    if args.diagnostic_sample_dir and not args.jpeg_cpu_diagnostic:
        parser.error('--diagnostic-sample-dir requires --jpeg-cpu-diagnostic')
    if args.jpeg_cpu_diagnostic:
        args.diagnostic_sample_dir = args.diagnostic_sample_dir or os.environ.get('NX300M_DIAGNOSTIC_SAMPLE_DIR')
        args.jpeg_task_trace = True
        if args.card_image == '/opt/nx300m-lab/media/nx300m-capture-color.img':
            args.card_image = '/opt/nx300m-lab/media/nx300m-jpeg-diagnostic.img'
    output = args.output_dir.resolve()
    if args.worker:
        raise SystemExit(worker(output,args.protected_pid,args.startup_seconds,args.capture_seconds))
    if os.name != 'nt': parser.error('Launch this tool using the project Conda on Windows')
    if not 5 <= args.startup_seconds <= 60 or not 5 <= args.capture_seconds <= 300:
        parser.error('Use bounded startup5..60 and capture5..300 seconds')
    if output == ROOT/'outputs/firmware-runtime': parser.error('Use an independent output directory')
    main_session = json.loads((ROOT/'outputs/firmware-runtime/session.json').read_text(encoding='utf-8'))
    protected = int(main_session['namespace_pid'])
    # Only this tool's dedicated image is accepted by default; a user-supplied
    # image is checked against both the main card and the established lab card.
    if args.card_image in ('/opt/nx300m-lab/media/nx300m-card-1g.img',
                            '/opt/nx300m-lab/media/nx300m-capture-lab.img'):
        parser.error('Use a dedicated capture-color card image')
    output.mkdir(parents=True,exist_ok=True)
    old = output/'session.json'
    if old.exists():
        raise RuntimeError('This output already contains a session; preserve it and choose a new --output-dir')
    base = ROOT/'outputs/firmware-runtime'
    command = [sys.executable,'-u',str(ROOT/'tools/run_stock.py'),'--program','camera',
               '--display','--preferences','--gpio-preview','--device-preview',
               '--shutter-preview','--shutter-park','10','--isp-preview','--cpu-ycc-resize',
               '--spi-mmio','--sensor-registers','--d4c-registers','--trace-startup',
               '--capture-boot','--mmap-script-fix','--ui-mod','--keep-awake',
               '--shutter-motion','ideal-8-sector','--pwm-timing',
               '--bayer-replay',str(base/'pp-active-bayer-fixture.bin'),
               '--sdcard','--sdcard-image',args.card_image,'--isp-color-observer',
               '--seconds','0','--tag','capture-color','--output-dir',str(output)]
    if not args.strace: command.append('--no-strace')
    if args.capture_observer: command.append('--capture-observer')
    if args.capture_geometry: command.append('--capture-geometry')
    if args.jpeg_task_trace: command.append('--jpeg-task-trace')
    if args.jpeg_cpu_diagnostic: command.append('--jpeg-cpu-diagnostic')
    if args.software_isp_source:
        command.extend(['--software-isp-source',str(args.software_isp_source.resolve())])
    if args.lut_look:
        command.append('--lut-port')
    if args.diagnostic_sample_dir:
        command.extend(['--diagnostic-sample-dir',args.diagnostic_sample_dir])
    from run_stock import diagnostic_sample_source
    sample_source = diagnostic_sample_source(args.diagnostic_sample_dir)
    from pipeline_state import stock_layout
    write_json(output/'pipeline-layout.json',stock_layout())
    write_json(output/'launch.json',{'command':command,'protected_main_namespace':protected,
                                    'card_image':args.card_image,
                                    'startup_seconds':args.startup_seconds,
                                    'capture_seconds':args.capture_seconds,
                                    'lut_request':{'id':args.lut_look,'strength':args.lut_strength} if args.lut_look else None,
                                    'diagnostic_sample_source':sample_source,
                                    'reference':'outputs/firmware-runtime/bayer-readout-development.json'})
    launched = subprocess.run(command,cwd=ROOT,capture_output=True,timeout=60)
    (output/'launcher.stdout.log').write_bytes(launched.stdout)
    (output/'launcher.stderr.log').write_bytes(launched.stderr)
    if launched.returncode:
        raise RuntimeError(f'Independent camera launch exit{launched.returncode}: {launched.stderr.decode("utf-8","replace")}')
    deadline = time.monotonic()+30
    while not old.exists() and time.monotonic() < deadline: time.sleep(.2)
    if not old.exists(): raise RuntimeError('Independent launch did not publish its session identity')
    result = subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',
                             linux_path(ROOT/'tools/probe_capture_color.py'),'--worker',
                             '--output-dir',linux_path(output),'--protected-pid',str(protected),
                             '--startup-seconds',str(args.startup_seconds),
                             '--capture-seconds',str(args.capture_seconds)],
                            cwd=ROOT,capture_output=True,
                            timeout=args.startup_seconds+args.capture_seconds+45)
    (output/'worker.stdout.log').write_bytes(result.stdout)
    (output/'worker.stderr.log').write_bytes(result.stderr)
    print(result.stdout.decode('utf-8','replace'))
    if result.returncode:
        raise RuntimeError(f'Independent capture worker exit{result.returncode}: {result.stderr.decode("utf-8","replace")}')
    report_path=output/'capture.json'
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('saved_software_isp_jpegs'):
        from PIL import Image, JpegImagePlugin
        verified=[]
        for name in report['saved_software_isp_jpegs']:
            path=output/name
            with Image.open(path) as image:
                image.load();exif=image.getexif()
                verified.append({'file':name,'bytes':path.stat().st_size,'dimensions':list(image.size),
                    'jpeg_sampling':JpegImagePlugin.get_sampling(image),'make':str(exif.get(271,'')),
                    'model':str(exif.get(272,'')),'firmware':str(exif.get(305,'')),
                    'rgb_extrema':image.convert('RGB').getextrema()})
        report['new_capture_jpeg_verified']=True
        report['decoded_software_isp_jpegs']=verified
        report['verification_scope']='Independent host full decode of stock-saved JPEG; recorded real Bayer, explicit software ISP model; hardware/color parity unproven'
        write_json(report_path,report)
        print(json.dumps(verified,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
