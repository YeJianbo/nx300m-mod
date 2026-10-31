"""Read live stock pipeline objects without writing memory or calling callbacks."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from session_stock import GUEST, PROJECT, inspect_session, session_output, wsl_session_environment

LIBRARY = '/usr/lib/libcapture-fw-slpcam-nx300.so'
APPLICATION = '/usr/bin/di-camera-app-nx300'


def stock_layout():
    # The Windows Conda environment already supplies the ELF/ARM decoders.
    os.environ.setdefault('CONDA_PREFIX', sys.prefix)
    import io
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    from capstone.arm import ARM_OP_MEM, ARM_REG_PC, ARM_REG_R4, ARM_REG_R3, ARM_REG_R2
    from elftools.elf.elffile import ELFFile
    root = Path('//wsl.localhost/Debian'+GUEST)
    elf = ELFFile(io.BytesIO((root/LIBRARY.lstrip('/')).read_bytes()))
    symbols = {symbol.name: symbol for symbol in elf.get_section_by_name('.dynsym').iter_symbols()}

    def read(address, size):
        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_LOAD' and segment['p_vaddr'] <= address and address+size <= segment['p_vaddr']+segment['p_filesz']:
                offset = address-segment['p_vaddr']
                return segment.data()[offset:offset+size]
        raise ValueError('ELF address is outside file-backed segments')

    def word(address): return struct.unpack('<I', read(address, 4))[0]
    symbol = symbols['_ZN9CLiveview11GetInstanceEv']
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.detail = True
    instructions = list(decoder.disasm(read(symbol['st_value'], symbol['st_size']), symbol['st_value']))
    base = None
    instance = None
    # Resolve the original function's PC-relative static-object address, rather
    # than assuming a host or guest load address from a previous camera run.
    for index, instruction in enumerate(instructions[:-3]):
        operands = instruction.operands
        if instruction.mnemonic != 'ldr' or len(operands) != 2 or operands[1].type != ARM_OP_MEM or operands[1].mem.base != ARM_REG_PC:
            continue
        value = word(instruction.address+8+operands[1].mem.disp)
        following = instructions[index+1]
        if operands[0].reg == ARM_REG_R4 and following.mnemonic == 'add' and following.op_str == 'r4, pc, r4':
            base = (following.address+8+value)&0xffffffff
        if base is not None and following.mnemonic == 'add' and following.op_str == 'r3, r4, r3' and instructions[index+2].op_str == 'r0, r3' and instructions[index+3].mnemonic == 'pop':
            instance = (base+value)&0xffffffff
    if instance is None: raise ValueError('Stock CLiveview instance address could not be resolved')
    app = ELFFile(io.BytesIO((root/APPLICATION.lstrip('/')).read_bytes()))
    udd = ELFFile(io.BytesIO((root/'usr/lib/libudd.so.3.1').read_bytes()))
    udd_symbols = {symbol.name: symbol for symbol in udd.get_section_by_name('.dynsym').iter_symbols()}
    # d4_get_udd_open_count indexes a private uint32 array. Resolve both
    # PC-relative literals from the current stock ELF, not a prior load address.
    def udd_word(address):
        segment = next(seg for seg in udd.iter_segments() if seg['p_type'] == 'PT_LOAD'
                       and seg['p_vaddr'] <= address < seg['p_vaddr']+seg['p_filesz'])
        return struct.unpack_from('<I', segment.data(), address-segment['p_vaddr'])[0]
    symbol = udd_symbols['d4_get_udd_open_count']
    segment = next(seg for seg in udd.iter_segments() if seg['p_type'] == 'PT_LOAD'
                   and seg['p_vaddr'] <= symbol['st_value'] < seg['p_vaddr']+seg['p_filesz'])
    offset = symbol['st_value']-segment['p_vaddr']
    count_code = list(decoder.disasm(segment.data()[offset:offset+symbol['st_size']], symbol['st_value']))
    literals = {ins.operands[0].reg: (ins.address, udd_word(ins.address+8+ins.operands[1].mem.disp))
                for ins in count_code if ins.mnemonic == 'ldr' and len(ins.operands) == 2
                and ins.operands[1].type == ARM_OP_MEM and ins.operands[1].mem.base == ARM_REG_PC}
    if set(literals) != {ARM_REG_R3, ARM_REG_R2} or not any(ins.op_str == 'r3, [r3, r2, lsl #2]' for ins in count_code):
        raise ValueError('Unexpected stock UDD open-count accessor')
    counts = (literals[ARM_REG_R3][0]+12+literals[ARM_REG_R3][1]+literals[ARM_REG_R2][1])&0xffffffff
    display = ELFFile(io.BytesIO((root/'usr/lib/libmm-displayer.so').read_bytes()))
    display_symbols = {symbol.name: symbol for symbol in display.get_section_by_name('.dynsym').iter_symbols()}
    if app['e_type'] != 'ET_EXEC': raise ValueError('Expected the stock fixed-address executable')
    first = lambda image: min(segment['p_vaddr']&~4095 for segment in image.iter_segments() if segment['p_type'] == 'PT_LOAD')
    return {'application_first_load': first(app), 'library_first_load': first(elf),
            'rendezvous_slot': symbols['_ZN9SingletonI24CLiveviewStartCompletionE11m_pInstanceE']['st_value'],
            'rendezvous_vtable': symbols['_ZTV24CLiveviewStartCompletion']['st_value']+8,
            'liveview_object': instance, 'liveview_vtable': symbols['_ZTV9CLiveview']['st_value']+8,
            'udd_first_load': first(udd), 'ipcm_context_slot': udd_symbols['ipcm_ctx']['st_value'],
            'udd_open_counts': counts,
            'video_event_slot': symbols['_ZN18CVideoEventHandler11m_pInstanceE']['st_value'],
            'video_event_vtable': symbols['_ZTV18CVideoEventHandler']['st_value']+8,
            'capture_manager_slot': symbols['_ZN9SingletonI13CStateManagerE11m_pInstanceE']['st_value'],
            'capture_manager_vtable': symbols['_ZTV13CStateManager']['st_value']+8,
            'capture_counter_slot': symbols['_ZN9SingletonI15CCaptureCounterE11m_pInstanceE']['st_value'],
            'capture_counter_vtable': symbols['_ZTV15CCaptureCounter']['st_value']+8,
            'capture_vtables': {str(index): symbols[name]['st_value']+8 for index, name in enumerate((
                '_ZTV16CCaptureStateOff', '_ZTV17CCaptureStateIdle', '_ZTV20CCaptureStateCapture', '_ZTV18CCaptureStateMovie'))},
            'three_a_vtables': {name: symbols[name]['st_value']+8 for name in symbols if name.startswith('_ZTV') and 'C3AState' in name},
            'reserved_callback': symbols['_ZN19CFrontEndMsgHandler18m_reservedCallbackE']['st_value'],
            'resize_data': symbols['gst_resize_data']['st_value'],
            'sensor_mode_info': symbols['gCfPPSensorModeInfo']['st_value'],
            'display_first_load': first(display),
            'display_slot': display_symbols['_ZN11CDisplayLCD11m_pInstanceE']['st_value'],
            'display_vtable': display_symbols['_ZTV11CDisplayLCD']['st_value']+8,
            'frontend_counters': {name: symbols['_ZN18CFrontEndInterrupt'+suffix]['st_value'] for name, suffix in (
                ('interrupt_control', '22m_interruptControlFlagE'), ('b2y', '6m_cB2yE'),
                ('b2y_address_updates', '16m_cB2yAddrUpdateE'), ('vd_rising', '11m_cVdRisingE'),
                ('vd_falling', '12m_cVdFallingE'))}}


def read_state(layout):
    state = inspect_session()
    if not state['running']: raise RuntimeError('Original camera is not running')
    pid = state['camera_pids'][0]
    proc = Path('/proc')/str(pid)
    if APPLICATION.encode() not in (proc/'cmdline').read_bytes().split(b'\0'):
        raise RuntimeError('Camera PID no longer identifies the stock executable')
    maps = []
    for line in (proc/'maps').read_text().splitlines():
        fields = line.split(None, 5)
        start, end = fields[0].split('-')
        maps.append((int(start, 16), int(end, 16), fields[1], int(fields[2], 16), fields[5] if len(fields) == 6 else ''))

    def bias(path, first):
        return next(start for start, _, _, offset, name in maps if offset == 0 and name == GUEST+path)-first

    guest_base = bias(APPLICATION, layout['application_first_load'])
    library = bias(LIBRARY, layout['library_first_load'])
    udd = bias('/usr/lib/libudd.so.3.1', layout['udd_first_load'])
    display = bias('/usr/lib/libmm-displayer.so', layout['display_first_load'])
    with (proc/'mem').open('rb', buffering=0) as memory:
        def read(address, size):
            if not any(start <= address and address+size <= end and 'r' in permissions for start, end, permissions, _, _ in maps):
                raise ValueError(f'Target object {address:#x}+{size:#x} is outside readable process mappings')
            memory.seek(address)
            data = memory.read(size)
            if len(data) != size: raise ValueError('Short process-memory read')
            return data

        def word(address): return struct.unpack('<I', read(address, 4))[0]
        display_pointer = word(display+layout['display_slot'])
        display_state = {'constructed': bool(display_pointer)}
        if display_pointer:
            if word(guest_base+display_pointer) != display-guest_base+layout['display_vtable']:
                raise ValueError('LCD display object does not have its stock vtable')
            descriptor = word(guest_base+display_pointer+0xc)
            display_state.update({'vtable_verified': True, 'framebuffer_fd': descriptor if descriptor < 0x80000000 else descriptor-0x100000000})
        pointer = word(library+layout['rendezvous_slot'])
        result = {'namespace_pid': state['namespace_pid'], 'camera_pid': pid,
                  'source': 'read-only stock process memory and current ELF mappings',
                  'memory_written': False, 'callbacks_invoked': False, 'synchronized_snapshot': False,
                  'liveview_start': {'constructed': bool(pointer)}, 'image_verified': False}
        result['lcd_display'] = display_state
        open_counts = {name: word(udd+layout['udd_open_counts']+index*4)
                       for name, index in (('ipcm', 5), ('ipcs', 6))}
        result['udd_open_counts'] = open_counts
        # Stock getters index an 80-byte mode record; these eight fields are
        # verified in cf_pp_sensor_get_* ARM accessors, not guessed from EXIF.
        sensor_mode = struct.unpack('<8I', read(library+layout['sensor_mode_info']+3*80, 32))
        result['sensor_still_mode'] = dict(zip(('mode', 'channels', 'global_width', 'global_height',
                                               'offset_x', 'offset_y', 'effective_width', 'effective_height'), sensor_mode))
        manager = word(library+layout['capture_manager_slot'])
        result['capture'] = {'constructed': bool(manager)}
        if manager:
            if word(guest_base+manager) != library-guest_base+layout['capture_manager_vtable']:
                raise ValueError('Capture manager does not have its stock vtable')
            capture_id = word(guest_base+manager+0x18)
            info = {'vtable_verified': True, 'raw_state': capture_id,
                    'state': ('off', 'idle', 'capture', 'movie')[capture_id] if capture_id < 4 else 'uninitialized'}
            if capture_id < 4:
                handler = word(guest_base+manager+4+capture_id*4)
                if word(guest_base+handler) != library-guest_base+layout['capture_vtables'][str(capture_id)]:
                    raise ValueError('Capture handler does not have its expected stock vtable')
                controller = word(guest_base+handler+0xc)
                if controller:
                    three_a_id = word(guest_base+controller+0x2c)
                    info['three_a_raw_state'] = three_a_id
                    if three_a_id < 8:
                        three_a_state = word(guest_base+controller+8+three_a_id*4)
                        vtable = word(guest_base+three_a_state)
                        info['three_a_handler'] = next((name for name, address in layout['three_a_vtables'].items()
                                                       if vtable == library-guest_base+address), 'unknown-vtable')
            result['capture'].update(info)
        # These counters describe backend work, not saved photographs. Stock
        # returns to capture-idle before encoding, and clearAllCounter can make
        # every pair equal without any successful output.
        if 'capture_counter_slot' in layout:
            counter = word(library+layout['capture_counter_slot'])
            result['capture_work'] = {'constructed': bool(counter), 'photo_completion_proven': False}
            if counter:
                data = read(guest_base+counter, 0x30)
                values = struct.unpack('<12I', data)
                if values[0] != library-guest_base+layout['capture_counter_vtable']:
                    raise ValueError('Capture work counter does not have its stock vtable')
                result['capture_work'].update({
                    'vtable_verified': True,
                    'raw_queue_drained': values[1] == values[3],
                    'pp_queue_drained': values[4] == values[5],
                    'ycc_queue_drained': values[7] == values[9],
                    'encoding_completed_work': values[10],
                    'encoding_total_work': values[11],
                    'encoding_queue_drained': values[10] == values[11],
                    'scope': 'current work counters; reset and zero==zero do not establish capture success'})
        if pointer:
            data = read(guest_base+pointer, 32)
            if struct.unpack_from('<I', data)[0] != library-guest_base+layout['rendezvous_vtable']:
                raise ValueError('Liveview completion object does not have its stock vtable')
            count = struct.unpack_from('<I', data, 8)[0]
            bits = struct.unpack_from('<I', data, 28)[0]
            if count > 8 or bits & ~255: raise ValueError('Unexpected stock rendezvous layout')
            pending = [index for index in range(count) if bits&(1<<index)]
            result['liveview_start'].update({'condition_count': count, 'pending_mask': f'0x{bits:02x}',
                                             'pending_indices': pending, 'vtable_verified': True,
                                             'waiting_for_start_response': 5 in pending})
        liveview = read(library+layout['liveview_object'], 64)
        if struct.unpack_from('<I', liveview)[0] != library-guest_base+layout['liveview_vtable']:
            raise ValueError('CLiveview static object does not have its stock vtable')
        result['liveview'] = {'vtable_verified': True, 'raw_state': struct.unpack_from('<I', liveview, 0x34)[0]}
        result['frontend_interrupts'] = {name: word(library+address) for name, address in layout['frontend_counters'].items()}
        result['reserved_start_callback'] = word(library+layout['reserved_callback'])
        resize_words = struct.unpack('<62I', read(library+layout['resize_data'], 248))
        result['ipcm_combi_resize'] = {'source_flags': list(resize_words[:3]),
            'images': [dict(zip(('width', 'height', 'stride_y', 'stride_c', 'ycc_type', 'subsampling', 'scan_type',
                                'y0', 'y1', 'c0', 'c1', 'offset_x', 'offset_y', 'reverse_cbcr', 'nlc', 'nlc_rc'),
                               resize_words[3+i*16:19+i*16])) for i in range(3)],
            'switches': list(resize_words[51:57])}
        video_event = word(library+layout['video_event_slot'])
        if video_event:
            if word(guest_base+video_event) != library-guest_base+layout['video_event_vtable']:
                raise ValueError('Video event object does not have its stock vtable')
            result['video_event'] = {'pending_events': f'0x{word(guest_base+video_event+0x40):08x}',
                                     'stream_switch': word(guest_base+video_event+0x44),
                                     'frame_count': word(guest_base+video_event+0x104),
                                     'b2y_buffer_registered': bool(word(guest_base+video_event+0x90)),
                                     'b2y_buffer_index': word(guest_base+video_event+0x94),
                                     'last_event': f'0x{word(guest_base+video_event+0x138):08x}'}
        # Samsung ipcm_type.h: context[0] -> ipcm_liveview_info. Read the
        # original configuration after libudd's virtual-to-physical conversion.
        # Stock ipcm_close frees ipcm_ctx without clearing its exported pointer.
        # A closed module has no live configuration even if that pointer is set.
        context = word(udd+layout['ipcm_context_slot']) if open_counts['ipcm'] else 0
        configuration = word(guest_base+context) if context else 0
        if configuration:
            data = read(guest_base+configuration, 0xf4)
            words = struct.unpack('<61I', data)
            channels = []
            for index, name in enumerate(('main', 'resize', 'subresize')):
                ycc = words[5+index*16:21+index*16]
                channels.append({'channel': name, 'width': ycc[0], 'height': ycc[1],
                                 'stride_y': ycc[2], 'stride_c': ycc[3], 'ycc_type': ycc[4],
                                 'scan_type': ycc[6], 'y0': f'0x{ycc[7]:08x}', 'y1': f'0x{ycc[8]:08x}',
                                 'c0': f'0x{ycc[9]:08x}', 'c1': f'0x{ycc[10]:08x}',
                                 'reverse_cbcr': ycc[13], 'nlc': ycc[14],
                                 'output_switch': words[53+index], 'wdma_switch': words[56+index]})
            result['ipcm_liveview'] = {'configured': True, 'open_count': open_counts['ipcm'], 'source_width': words[0], 'source_height': words[1],
                                       'source_input_channel': words[2], 'head_color': words[3],
                                       'bayer_packed': words[4], 'addresses_are_initial_configuration': True, 'outputs': channels}
        else: result['ipcm_liveview'] = {'configured': False, 'open_count': open_counts['ipcm']}
        return result


def wait_for_ipcm(layout, seconds):
    deadline = time.monotonic()+seconds
    while True:
        try:
            result = read_state(layout)
            configuration = result.get('ipcm_liveview', {})
            if (configuration.get('configured') and configuration['outputs'][0]['wdma_switch'] == 1
                    and result['frontend_interrupts']['interrupt_control']&0x20000): return result
            if time.monotonic() >= deadline: raise RuntimeError('Stock IPCM output did not become ready')
        except (RuntimeError, ValueError, OSError, StopIteration):
            if time.monotonic() >= deadline: raise
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', help=argparse.SUPPRESS)
    parser.add_argument('--wait-ipcm', type=float, default=0, help='Wait up to this many seconds for the stock IPCM configuration (0..30)')
    args = parser.parse_args()
    if not 0 <= args.wait_ipcm <= 30: parser.error('--wait-ipcm must be 0..30')
    if os.name == 'nt':
        script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/pipeline_state.py'
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', *wsl_session_environment(), 'python3', script, '--layout', json.dumps(stock_layout()), '--wait-ipcm', str(args.wait_ipcm)],
                                capture_output=True, timeout=args.wait_ipcm+20)
        if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
        print(result.stdout.decode('utf-8').strip())
        return
    if not args.layout: parser.error('Run this command from the Windows Conda environment to resolve the stock ELF layout')
    layout = json.loads(args.layout)
    result = wait_for_ipcm(layout, args.wait_ipcm) if args.wait_ipcm else read_state(layout)
    (session_output()/'pipeline-state.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
