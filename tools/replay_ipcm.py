"""Replay a recorded image at the IPCM WDMA output boundary in the private lab.

This is an explicit input scenario for buffer/callback/display development.
It does not run the sensor, Bayer ISP or still LUT processing.
"""
import argparse
import json
import itertools
import os
from pathlib import Path
import struct
import subprocess
import time

from pipeline_state import read_state, stock_layout, wait_for_ipcm
from session_stock import GUEST, PROJECT, session_output, wsl_session_environment

SMA_BASE, SMA_BYTES = 0xc6800000, 0x18b00000


def publish_report(report, runtime=None):
    path = session_output()/'ipcm-replay.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    if runtime:
        status = {key: report[key] for key in ('namespace_pid', 'camera_pid', 'worker_pid', 'stage', 'continuous', 'frames_written')}
        status['last_submit_monotonic'] = report.get('last_submit_monotonic', 0)
        status['frame_sync'] = report.get('frame_sync')
        if 'error' in report: status['error'] = report['error']
        path = runtime/'nx300m-ipcm-replay-status.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(status)+'\n')
        temporary.replace(path)


def main_output(state):
    pipeline = state.get('ipcm_liveview', {})
    if not pipeline.get('configured'): raise RuntimeError('Stock IPCM liveview is not configured')
    output = pipeline['outputs'][0]
    if output['output_switch'] != 1 or output['wdma_switch'] != 1:
        raise RuntimeError('Stock IPCM main WDMA is not enabled')
    if output['scan_type'] != 1 or output['nlc'] or output['ycc_type'] not in (0, 1):
        raise RuntimeError('Replay currently requires progressive, uncompressed YUV420/422')
    width, height = output['width'], output['height']
    if not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1:
        raise RuntimeError('Unsupported IPCM image dimensions')
    if min(output['stride_y'], output['stride_c']) < width:
        raise RuntimeError('IPCM plane stride is smaller than its active width')
    return output


def main_output_enabled(state):
    pipeline = state.get('ipcm_liveview', {})
    if not pipeline.get('configured'): return False
    output = pipeline['outputs'][0]
    return output['output_switch'] == 1 and output['wdma_switch'] == 1


def chroma_order(info, *, recorded=False):
    """Byte order of the explicit replay source, not a sensor color model.

    This lab's reverse=1 preview route feeds its CbCr LCD decoder. This is a
    replay convention, not a measured hardware NV12/NV21 guarantee. The old
    packet writer mistook that output switch for an absolute CrCb format.
    Untagged packets were written by that old code and must be normalized.
    """
    if recorded:
        order = info.get('chroma_order')
        if order is not None:
            if order not in ('CbCr', 'CrCb'):
                raise ValueError('Unknown recorded chroma order')
            return order
        return 'CrCb' if info['reverse_cbcr'] else 'CbCr'
    return 'CbCr' if info['reverse_cbcr'] else 'CrCb'


def prepare_image(path, output):
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        source_size = image.size
        image = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'),
                             (output['width'], output['height']), method=Image.Resampling.LANCZOS)
        y, cb, cr = image.convert('YCbCr').split()
        size = (output['width']//2, output['height']//(2 if output['ycc_type'] == 0 else 1))
        cb = cb.resize(size, Image.Resampling.BOX).tobytes()
        cr = cr.resize(size, Image.Resampling.BOX).tobytes()
        chroma = bytearray(len(cb)*2)
        chroma[0::2], chroma[1::2] = (cb, cr) if chroma_order(output) == 'CbCr' else (cr, cb)
        payload = y.tobytes()+chroma
    return payload, {'path': str(path.resolve()), 'size': source_size,
                     'encoding': 'Pillow BT.601 full-range YCbCr; box chroma subsampling',
                     'resize': 'aspect-preserving center crop to stock active output size',
                     'color_calibrated': False}


def adapt_recorded_input(payload, source, target):
    """Resample the recorded YCC input when stock changes its stream geometry.

    This is input preparation, not an emulated Bayer/ISP operation. Chroma is
    sampled in its own grid; even centered crops preserve subsampling parity.
    """
    sw, sh = source['width'], source['height']
    tw, th = target['width'], target['height']
    for width, height in ((sw, sh), (tw, th)):
        if not 0 < width <= 8192 or not 0 < height <= 8192 or (width|height)&1:
            raise ValueError('Invalid recorded-input geometry')
    sch = sh//(2 if source['ycc_type'] == 0 else 1)
    tch = th//(2 if target['ycc_type'] == 0 else 1)
    if len(payload) != sw*(sh+sch): raise ValueError('Invalid recorded-input planes')
    cw, ch = sw, sh
    if sw*th > sh*tw: cw = (sh*tw//th)&~1
    else: ch = (sw*th//tw)&~1
    if min(cw, ch) < 2: raise ValueError('Recorded crop is too narrow')
    ox, oy = ((sw-cw)//2)&~1, ((sh-ch)//2)&~1
    result = bytearray()
    view = memoryview(payload)
    for chroma in (False, True):
        components = 2 if chroma else 1
        divisor = 2 if chroma and source['ycc_type'] == 0 else 1
        width, height = tw//components, tch if chroma else th
        cropw, croph = cw//components, ch//divisor
        xmap = [ox//components+min(cropw-1, (2*x+1)*cropw//(2*width)) for x in range(width)]
        ymap = [oy//divisor+min(croph-1, (2*y+1)*croph//(2*height)) for y in range(height)]
        offset = sw*sh if chroma else 0
        reverse = chroma and chroma_order(source, recorded=True) != chroma_order(target)
        for y in ymap:
            row = view[offset+y*sw:offset+(y+1)*sw]
            for x in xmap:
                for c in range(components): result.append(row[x*components+(1-c if reverse else c)])
    return result


def _submit_frames(layout, packet, metadata, frames, fps, report):
    before = report['before']
    camera, namespace = before['camera_pid'], before['namespace_pid']
    root = Path('/proc')/str(namespace)/'root'/GUEST.lstrip('/')
    payload = packet.read_bytes()
    recorded_payload = payload
    format_keys = ('width', 'height', 'ycc_type', 'reverse_cbcr')
    active_format = tuple(metadata[key] for key in format_keys)
    active_order = chroma_order(metadata, recorded=True)
    width, height, ycc = (metadata[key] for key in ('width', 'height', 'ycc_type'))
    chroma_height = height//(2 if ycc == 0 else 1)
    if len(payload) != width*(height+chroma_height): raise ValueError('Invalid recorded image byte count')
    # The target is the simulator's bounded shared SMA file, never /proc/mem.
    import fcntl
    with (root/'var/run/nx300m-ipcm-replay.lock').open('a') as ownership:
        fcntl.flock(ownership, fcntl.LOCK_EX|fcntl.LOCK_NB)
        dma = os.open(root/'tmp/nx-sma', os.O_RDWR|os.O_NOFOLLOW)
        notify = os.open(root/'var/run/nx300m-ipcm-events', os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        sync = os.open(root/'var/run/nx300m-ssif-events', os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        records = report['frames']
        sync_high = False
        period = 1/fps
        report['frame_sync'] = {'source': 'explicit recorded-frame clock', 'fps': fps,
                                'rising': 0, 'falling': 0, 'late_falling': 0}

        def falling_edge(iteration_started):
            deadline = iteration_started+period/2
            remaining = deadline-time.monotonic()
            if remaining > 0: time.sleep(remaining)
            elif remaining < -period/2: report['frame_sync']['late_falling'] += 1
            if os.write(sync, b'0\n') != 2: raise RuntimeError('Short recorded falling-sync input')
            report['frame_sync']['falling'] += 1

        def finish_period(iteration_started):
            deadline = iteration_started+period
            if deadline > time.monotonic(): time.sleep(deadline-time.monotonic())
        try:
            if os.fstat(dma).st_size != SMA_BYTES: raise RuntimeError('Unexpected simulator SMA extent')
            for index in (range(frames) if frames else itertools.count()):
                iteration_started = time.monotonic()
                state = read_state(layout)
                if (state['camera_pid'], state['namespace_pid']) != (camera, namespace):
                    raise RuntimeError('Stock camera session changed during replay')
                # Original VD rising -> address-update alarm -> original buffer
                # selection must precede the WDMA write and B2Y notification.
                previous = state['frontend_interrupts']['b2y_address_updates']
                if os.write(sync, b'1\n') != 2: raise RuntimeError('Short recorded frame-sync input')
                sync_high = True
                report['frame_sync']['rising'] += 1
                deadline = iteration_started+period/2
                registered = False
                while state['ipcm_liveview']['configured']:
                    state = read_state(layout)
                    if (main_output_enabled(state) and
                            state['frontend_interrupts']['b2y_address_updates'] > previous and state['video_event']['b2y_buffer_registered']):
                        registered = True
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
                if not registered:
                    falling_edge(iteration_started)
                    sync_high = False
                    if frames: raise RuntimeError('Original VD/address-update path did not register a frame buffer')
                    report.update(stage='waiting-stock-buffer' if state['ipcm_liveview']['configured'] else 'waiting-stock-ipcm', last_state=state)
                    report['buffer_waits'] = report.get('buffer_waits', 0)+1
                    publish_report(report, root/'var/run')
                    # Mode changes can temporarily withdraw the stock WR
                    # buffer. Supply no pixels or B2Y until a new VD registers
                    # one; keep the continuous source available for its return.
                    finish_period(iteration_started)
                    continue
                output = main_output(state)
                requested_format = tuple(output[key] for key in format_keys)
                requested_order = chroma_order(output)
                if requested_format != active_format or requested_order != active_order:
                    payload = adapt_recorded_input(recorded_payload, metadata, output)
                    width, height, ycc = (output[key] for key in ('width', 'height', 'ycc_type'))
                    chroma_height = height//(2 if ycc == 0 else 1)
                    active_format = requested_format
                    active_order = requested_order
                    report['active_input'] = {key: output[key] for key in format_keys} | {'chroma_order': active_order}
                    report['input_resampling'] = 'even-centered crop and nearest-neighbor planar YCC'
                # Mode switching can close IPCM while the recorded image is
                # being resampled. Do not write through its retired WR buffer.
                current = read_state(layout)
                if (not main_output_enabled(current) or
                        not current.get('video_event', {}).get('b2y_buffer_registered') or
                        current['frontend_interrupts']['b2y_address_updates'] != state['frontend_interrupts']['b2y_address_updates']):
                    falling_edge(iteration_started)
                    sync_high = False
                    if frames: raise RuntimeError('Stock IPCM buffer changed before recorded DMA write')
                    report.update(stage='waiting-stock-buffer', last_state=current)
                    publish_report(report, root/'var/run')
                    finish_period(iteration_started)
                    continue
                bank = (root/'tmp/nx-isp-ipcm').read_bytes()
                word = lambda offset: struct.unpack_from('<I', bank, offset)[0]
                if not word(0)&0x10000 or not word(8)&1:
                    falling_edge(iteration_started)
                    sync_high = False
                    if frames: raise RuntimeError('IPCM main output or its interrupt is disabled')
                    report.update(stage='waiting-stock-buffer', last_state=current)
                    publish_report(report, root/'var/run')
                    finish_period(iteration_started)
                    continue
                y0, y1, sy = word(0xd0c), word(0xd10), word(0xd14)&0x1ffff
                c0, c1, sc = word(0xd18), word(0xd1c), word(0xd20)&0x1ffff
                # ipcm_change_output_addr updates the WDMA register bank; the
                # original ipcm_liveview_info retains its initial addresses.
                if y1 != y0+output['stride_y'] or c1 != c0+output['stride_c'] or sy != 2*output['stride_y'] or sc != 2*output['stride_c']:
                    raise RuntimeError('Unsupported IPCM even/odd WDMA address layout')
                rows = []
                for first, odd, stride, count, source in ((y0, y1, sy, height, 0), (c0, c1, sc, chroma_height, width*height)):
                    for row in range(count):
                        address = (odd if row&1 else first)+(row//2)*stride
                        if not SMA_BASE <= address or address+width > SMA_BASE+SMA_BYTES:
                            raise RuntimeError('IPCM output is outside simulator SMA storage')
                        rows.append((address-SMA_BASE, source+row*width))
                for address, source in rows:
                    if os.pwrite(dma, payload[source:source+width], address) != width:
                        raise RuntimeError('Short simulator DMA write')
                if os.write(notify, b'18\n') != 3: raise RuntimeError('Short IPCM queue notification')
                falling_edge(iteration_started)
                sync_high = False
                records.append({'frame': report['frames_written'], 'y0': hex(y0), 'c0': hex(c0), 'bytes_written': len(payload)})
                report['frames_written'] += 1
                report['last_submit_monotonic'] = time.monotonic()
                report['simulator_shared_dma_written'] = True
                if not frames:
                    if len(records) > 60: del records[0]
                    if report['frames_written']%5 == 1:
                        report.update(stage='running', last_state=state)
                        publish_report(report, root/'var/run')
                finish_period(iteration_started)
        finally:
            if sync_high:
                try: os.write(sync, b'0\n')
                except OSError: pass
            os.close(notify)
            os.close(sync)
            os.close(dma)
    return read_state(layout)


def submit(layout, packet, metadata, frames, fps, *, resume=False):
    before = read_state(layout) if resume else wait_for_ipcm(layout, 10)
    report = {'source': metadata['source'], 'input_boundary': 'recorded IPCM main WDMA output',
              'namespace_pid': before['namespace_pid'], 'camera_pid': before['camera_pid'], 'frames': [],
              'worker_pid': int(Path('/proc/self/stat').read_text().split()[0]),
              'continuous': not frames, 'frames_written': 0, 'stage': 'starting',
              'before': before, 'proc_mem_written': False, 'simulator_shared_dma_written': False,
              'sensor_emulated': False, 'sensor_bayer_isp_executed': False, 'still_lut_applied': False,
              'display_image_verified': False}
    try:
        report['after'] = _submit_frames(layout, packet, metadata, frames, fps, report)
        report['stage'] = 'submitted'
    except BaseException as error:
        report.update(stage='failed', error=str(error))
        raise
    finally:
        report['simulator_shared_dma_written'] = bool(report['frames'])
        runtime = Path('/proc')/str(before['namespace_pid'])/'root'/GUEST.lstrip('/')/'var/run'
        publish_report(report, runtime if runtime.exists() else None)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--frames', type=int, default=10)
    parser.add_argument('--fps', type=float, default=5)
    parser.add_argument('--submit', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--layout', help=argparse.SUPPRESS)
    parser.add_argument('--resume', action='store_true', help='Resume input in the existing session even while stock IPCM is closed')
    args = parser.parse_args()
    if not 0 <= args.frames <= 300 or not 1 <= args.fps <= 30: parser.error('Use 1..300 frames at 1..30 fps (0 means continuous for --submit)')
    if args.frames == 0 and not args.submit: parser.error('Continuous input is started by run_stock.py --replay-continuous')
    if args.resume and not args.submit: parser.error('--resume requires --submit in the current camera namespace')
    if args.submit:
        if not args.layout: parser.error('--submit requires --layout; use --image from the Windows Conda environment')
        metadata = json.loads(args.submit.read_text(encoding='utf-8'))
        report = submit(json.loads(args.layout), args.submit.with_suffix('.yuv'), metadata, args.frames, args.fps, resume=args.resume)
        print(json.dumps({'frames_written': len(report['frames']), 'before': report['before']['liveview_start'],
                          'after': report['after']['liveview_start'], 'display_image_verified': False}))
        return
    if os.name != 'nt' or not args.image: parser.error('Run --image from the Windows Conda environment')
    layout = stock_layout()
    script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/pipeline_state.py'
    snapshot = subprocess.run(['wsl.exe', '-u', 'root', '-e', *wsl_session_environment(), 'python3', script, '--layout', json.dumps(layout), '--wait-ipcm', '10'],
                              capture_output=True, timeout=25)
    if snapshot.returncode: raise RuntimeError(snapshot.stderr.decode('utf-8', 'replace').strip())
    output = main_output(json.loads(snapshot.stdout))
    payload, source = prepare_image(args.image, output)
    packet = session_output()/'ipcm-replay-input.json'
    packet.with_suffix('.yuv').write_bytes(payload)
    packet.write_text(json.dumps({key: output[key] for key in ('width', 'height', 'ycc_type', 'reverse_cbcr')} | {'chroma_order': chroma_order(output), 'source': source},
                                 ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    linux = lambda path: '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]
    command = ['wsl.exe', '-u', 'root', '-e', *wsl_session_environment(), 'python3', linux(Path(__file__).resolve()), '--submit', linux(packet),
               '--layout', json.dumps(layout), '--frames', str(args.frames), '--fps', str(args.fps)]
    result = subprocess.run(command, capture_output=True, timeout=args.frames/args.fps+30)
    print(result.stdout.decode('utf-8', 'replace').strip())
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())


if __name__ == '__main__': main()
