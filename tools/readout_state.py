"""Decode the stock PP transport snapshot taken at CSM readout arm time."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess

from session_stock import GUEST, PROJECT, inspect_session


def decode(data):
    if len(data) != 0xb000: raise ValueError('Expected the complete stock PP register bank')
    word = lambda offset: struct.unpack_from('<I', data, offset)[0]
    control = word(0x3314)
    bits_index = (control >> 4)&7
    bit_width = (8, 10, 12, 14, 16)[bits_index] if bits_index < 5 else None
    channels = (control >> 8)&7
    ssif = {name: {'offset': word(offset) >> 16, 'size': word(offset)&65535}
            for name, offset in (('total_vertical', 0x2960), ('optical_black_vertical', 0x2964),
                                 ('active_vertical', 0x2968))}
    # Stock code writes both horizontal fields into the high halfword with OR;
    # unlike vertical registers, it does not expose two independent values.
    ssif['horizontal_control_raw'] = f'0x{word(0x296c):08x}'
    result = {'source': 'original PP register snapshot at CSM arm time',
              'memory_written': False, 'callbacks_invoked': False, 'image_verified': False,
              'ssif_output': ssif,
              'wdma': {'control': f'0x{control:08x}', 'secc': bool(control&1), 'bit_width': bit_width,
                       'input_channels': channels, 'write_enable': (control >> 28)&7,
                       'clock_data_bits': word(0x3300),
                       'even_address': f'0x{word(0x3304):08x}', 'odd_address': f'0x{word(0x3308):08x}',
                       'delta_bytes': word(0x330c),
                       'second_even_address': f'0x{word(0x3324):08x}', 'second_odd_address': f'0x{word(0x3328):08x}',
                       'second_delta_bytes': word(0x332c)},
              'raw_registers': {f'0x{offset:04x}': f'0x{word(offset):08x}' for offset in
                                (*range(0x2960, 0x2984, 4), *range(0x3300, 0x333c, 4), 0x3a00, 0x3a10)}}
    return result


def collect(packet=None):
    state = inspect_session()
    if not state['namespace_pid']: raise RuntimeError('No private camera namespace')
    runtime = Path('/proc')/str(state['namespace_pid'])/'root'/GUEST.lstrip('/')/'var/run'
    data = (runtime/'nx300m-readout-pp.bin').read_bytes()
    target = PROJECT/'outputs/firmware-runtime/readout-pp.bin'
    target.write_bytes(data)
    report = decode(data)
    dimension_path = runtime/'nx300m-readout-dimensions.bin'
    if dimension_path.exists():
        report['sensor_mode_3'] = dict(zip(('global_width', 'global_height', 'effective_width', 'effective_height'),
                                          struct.unpack('<4I', dimension_path.read_bytes())))
    report.update(namespace_pid=state['namespace_pid'], current_camera_pids=state['camera_pids'],
                  snapshot_survives_camera_exit=True)
    if packet:
        from replay_bayer import inspect_packet
        info = inspect_packet(packet)
        expected = Path(packet).read_bytes()[64:]
        rows = []
        with (runtime.parent.parent/'tmp/nx-sma').open('rb') as memory:
            wdma = report['wdma']
            for row in range(info['height']):
                address = int(wdma['odd_address' if row&1 else 'even_address'], 16)+(row//2)*wdma['delta_bytes']
                offset = address-0xc6800000
                if offset < 0 or offset+info['stride'] > 0x18b00000: raise ValueError('Readback outside SMA')
                memory.seek(offset); rows.append(memory.read(info['stride']))
        actual = b''.join(rows)
        report['recorded_payload_readback'] = {'packet': str(packet), 'bytes': len(actual),
                                               'bytes_equal': actual == expected, 'synchronized_snapshot': False}
    target.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Decode an existing snapshot instead of reading the session')
    parser.add_argument('--compare', type=Path, help='Read programmed SMA rows and compare against a recorded Bayer packet')
    args = parser.parse_args()
    if args.file:
        print(json.dumps(decode(args.file.read_bytes()), indent=2)); return
    if os.name == 'nt':
        script = '/mnt/'+PROJECT.drive[0].lower()+PROJECT.as_posix()[2:]+'/tools/readout_state.py'
        command = ['wsl.exe', '-u', 'root', '-e', 'python3', script]
        if args.compare:
            packet = args.compare.resolve()
            command.extend(('--compare', '/mnt/'+packet.drive[0].lower()+packet.as_posix()[2:]))
        result = subprocess.run(command, capture_output=True, timeout=20)
        if result.returncode: raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
        print(result.stdout.decode('utf-8').strip()); return
    print(json.dumps(collect(args.compare), indent=2))


if __name__ == '__main__': main()
