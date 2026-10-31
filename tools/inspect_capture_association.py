"""Read selected capture-to-save functions in three identified stock ELF files.

Static only. Does not launch a camera, write runtime code or scan other ELFs.
"""
import argparse
import json
from pathlib import Path

from inspect_gamma_hook import Stock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/firmware-runtime/capture-association'
FUNCTIONS = {
    'libsif.so': [
        '_ZN14DscFileHandler7OperateEP13tImage_buffer',
        '_ZN14DscFileHandler8TaskMainEv', '_ZN14DscFileHandler13MemoryReleaseEv',
        '_ZN12DscComponent16PushToSuccessorsEP13tImage_buffer',
        '_ZN7DscJPEG8TaskMainEv', '_ZN13tImage_buffer4initE7eDscOps',
        '_ZN8DscUtils10FreeBufferEP13tImage_buffer',
        '_ZN8DscImage13SaveExifImageEPcj',
        '_ZN8DscImage6NotifyE22eDscImage_NotificationPFvlllEll',
        '_ZN7DscExif6EncodeEP13tImage_bufferS1_',
    ],
    'libmm-photo.so': [
        '_ZN15CImageGenerator19CaptureDataReceiverEiP17stCAPTURE_IMG_SETPvj',
        '_ZN15CImageGenerator14RunFromCaptureEv',
        '_ZN15CImageGenerator16CaptureStartDoneEv',
        '_ZN15CImageGenerator15CaptureSaveDoneEv',
        '_ZN12CCaptureMess17GenerateRandomKeyEv', '_ZN12CCaptureMess12GetRandomKeyEv',
        '_ZN12CCaptureMess14ClearRandomKeyEv',
    ],
    'libcapture-fw-slpcam-nx300.so': [
        '_ZN17CCapturePublisher18NotifyCaptureStartEv',
        '_ZN17CCapturePublisher7PublishEml',
        '_ZN17CCapturePublisher7PublishEmlP13stCapImageSet',
        '_ZN12CDriveSingle20setFrontEndParameterEv',
    ],
}


def collect(rootfs):
    report = {'scope': 'selected functions in three identified stock ELF files',
              'camera_started': False, 'main_session_accessed': False, 'libraries': {}}
    for library, functions in FUNCTIONS.items():
        stock = Stock(rootfs/'usr/lib'/library)
        dynsym = stock.elf.get_section_by_name('.dynsym')
        plt_section = stock.elf.get_section_by_name('.plt')
        plt = {}
        for i, relocation in enumerate(stock.elf.get_section_by_name('.rel.plt').iter_relocations()):
            plt[plt_section['sh_addr']+20+i*12] = {
                'symbol': dynsym.get_symbol(relocation['r_info_sym']).name,
                'got_slot': hex(relocation['r_offset']), 'relocation_type': relocation['r_info_type'],
            }
        lines, info = [], []
        for name in functions:
            symbol = stock.symbols[name]
            address, size = symbol['st_value'], symbol['st_size']
            calls = []
            lines.append(f'\n{name} @ {address:#x} size={size}:')
            for ins in stock.decoder.disasm(stock.read(address, size), address):
                annotation = ''
                if ins.mnemonic == 'bl':
                    target = int(ins.op_str[1:], 16)
                    binding = plt.get(target, {})
                    annotation = binding.get('symbol', '')
                    calls.append({'call': hex(ins.address), 'return': hex(ins.address+4),
                                  'target': hex(target), **binding})
                elif ins.mnemonic == 'blx':
                    calls.append({'call': hex(ins.address), 'indirect_register': ins.op_str})
                lines.append(f'{ins.address:08x}: {ins.mnemonic:8} {ins.op_str:38} {annotation}')
            info.append({'symbol': name, 'address': hex(address), 'size': size, 'calls': calls})
        (OUT/(library+'.asm')).write_text('\n'.join(lines)+'\n', encoding='utf-8')
        extra = {}
        if library == 'libmm-photo.so':
            bindings = []
            for load, add, sites in (
                (0x3c3c8, 0x3c3cc, ((0x3c690, 'CaptureImgDataSetQueue'),
                                    (0x3c704, 'CaptureExifQueue'), (0x3c720, 'CaptureMakerNoteQueue'))),
                (0x3dabc, 0x3dac0, ((0x3dae4, '_ZN15CImageGenerator11SIFListenerElll'),
                                    (0x3db08, '_ZN15CImageGenerator11SIFListenerElll'),
                                    (0x3db1c, 'CaptureExifQueue'), (0x3db4c, 'CaptureMakerNoteQueue'),
                                    (0x3dbb8, 'CaptureImgDataSetQueue'))),
            ):
                base = (add+8+stock.literal(load)[1]) & 0xffffffff
                for site, expected in sites:
                    slot = (base+stock.literal(site)[1]) & 0xffffffff
                    relocation = stock.relocs[slot]
                    if relocation['symbol'] != expected or relocation['type'] != 21:
                        raise ValueError(f'Queue/callback GOT identity changed at {site:#x}')
                    bindings.append({'literal_load': hex(site), 'got_slot': hex(slot), **relocation})
            extra['verified_queue_and_callback_bindings'] = bindings
        if library == 'libsif.so':
            bindings = []
            for vtable, offset, expected in (
                ('_ZTV7DscExif', 16, '_ZN7DscExif6EncodeEP13tImage_bufferS1_'),
                ('_ZTV14DscFileHandler', 20, '_ZN14DscFileHandler7OperateEP13tImage_buffer'),
            ):
                address = stock.symbols[vtable]['st_value']+offset
                relocation = stock.relocs[address]
                if relocation['symbol'] != expected or relocation['type'] != 2:
                    raise ValueError(f'Virtual operation identity changed: {vtable}')
                bindings.append({'vtable': vtable, 'entry': hex(address), **relocation})
            extra['verified_virtual_bindings'] = bindings
        report['libraries'][library] = {'path': str(stock.path), 'functions': info, **extra}
    report['association_proven'] = False
    report['verified_findings'] = [
        {'edge': 'JPEG input -> file-output object', 'library': 'libsif.so',
         'evidence': 'TaskMain 0x2aee0..0x2af5c allocates op=10 output, copies input+0x10/+0x14, sets descriptor callback, calls Encode(input,output); 0x2b084 forwards that output.'},
        {'edge': 'file-output object -> WriteChunks', 'library': 'libsif.so',
         'evidence': 'Operate 0x27b30 queues the same tImage_buffer pointer; TaskMain 0x27080 dequeues into self+0x64, then calls WriteChunks at 0x270b4.'},
        {'edge': 'end of descriptor lifetime', 'library': 'libsif.so',
         'evidence': 'TaskMain 0x27150 calls MemoryRelease; FreeBuffer 0x129f8..0x12a0c destroys/deletes sFileOp and clears image+0x2c, then marks image op=11 at 0x12ab0.'},
        {'edge': 'capture dataset -> image-generator processing', 'library': 'libmm-photo.so',
         'evidence': 'CaptureDataReceiver 0x3c6a0/0x3c714/0x3c730 pushes copied image/EXIF/makernote into three identified queues. RunFromCapture 0x3db28/0x3db58/0x3dbc4 reads those queue fronts and copies the image vector at 0x3dbd8.'},
        {'edge': 'image-generator processing -> save submission', 'library': 'libmm-photo.so',
         'evidence': 'RunFromCapture calls CopyExifImageData at 0x3e2d4/0x3e2f0/0x3e328/0x3e344, then SaveExifImage at 0x3e3ec. SaveExifImage queues its pending DscImage+0x48 object through virtual Operate at libsif 0x5bd30 and clears the pending pointer at 0x5bd48.'},
    ]
    report['rejected_identifiers'] = [
        {'candidate': 'capture-start event/zero argument',
         'counterexample': 'NotifyCaptureStart 0x257080..0x257090 publishes event 0x01010001,arg=0; CaptureStartDone 0x3d6fc..0x3d708 also calls back with zeros. Neither carries the output object or a request identity.'},
        {'candidate': 'CaptureDataReceiver callback context arguments',
         'counterexample': 'r2 void* and r3 uint32 are only stored at stack -0x3f8/-0x3fc; the reviewed receiver does not propagate them into the queues.'},
        {'candidate': 'tImage_buffer +0x10/+0x14 cookies',
         'counterexample': 'RunFromCapture registers Notify(type=2,SIFListener,3,0) at 0x3daf4. Notify stores constants at DscImage+0xd4/+0xd8, SaveExifImage copies these to image+0x10/+0x14, and TaskMain copies them to output. They are notification tags, not a per-capture ID.'},
        {'candidate': 'CCaptureMess RandomKey',
         'counterexample': 'GenerateRandomKey 0x547ac..0x547cc seeds with second-resolution time then takes random(). Equal seed/call sequence can repeat; no uniqueness guarantee or verified request-to-file propagation.'},
        {'candidate': 'bare pointer or filename',
         'counterexample': 'Receiver copies source data into new queue vectors and RunFromCapture copies again; these copies break source-pointer identity. Descriptor is destroyed after saving. A live pointer needs an allocation generation and invalidation; neither a stale address nor a new pathname identifies the external input.'},
    ]
    report['bounded_design'] = {
        'implementable_partial_scope': 'Assign observer-owned process-local generation IDs to complete live image lifecycles; record the exact Encode(input,output) edge, queued output, WriteChunks and FreeBuffer. Match queue producer/consumer by queue operations and copied item identity, not timestamps. Missing/ambiguous lifecycle records leave association unavailable.',
        'not_yet_established': 'The external request/synthetic shutter input -> accepted original capture-parameter generation -> the exact stCAPTURE_IMG_SET / queued CaptureImgDataSet item.',
        'specific_missing_hop': 'Need a verified field or explicit accepted-input callback connecting the same CProcessParameter/CCapImage generation to a CaptureDataReceiver item. Current reviewed start notification has no such identity and the receiver discards its context arguments; stCAPTURE_IMAGE payload field semantics for such an identity are unverified.',
        'stop': 'No runtime association implemented. Do not infer request ownership from idle, callback zeros, timing proximity, RandomKey or file appearance. No further ELF discovery in this pass.',
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, default=Path('//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15'))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = collect(args.rootfs)
    (OUT/'evidence.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (OUT/'findings.md').write_text('''# 拍摄输入到文件：关联边界

当前不能可靠证明外部请求到原厂文件的端到端关联。文件侧可以按实际对象生命周期建立链，前端请求身份仍缺明确一跳。

- `libmm-photo.so::CImageGenerator::CaptureDataReceiver` 把图像、EXIF、MakerNote分别复制入 `CaptureImgDataSetQueue`、`CaptureExifQueue`、`CaptureMakerNoteQueue`；`RunFromCapture` 读取同名队列。GOT relocation 已核验。
- `RunFromCapture` 在 `0x3e3ec` 调用 `SaveExifImage`。后者把 `DscImage+0x48` 的待处理对象入 JPEG 队列，随即清空该指针。
- `libsif.so::DscJPEG::TaskMain` 在 `0x2aee4` 分配 op=10 输出，将输入传给虚函数 `Encode(input,output)`，再交给 `PushToSuccessors`。`DscExif::Encode` 及 `DscFileHandler::Operate` 的 vtable relocation 均指向导出符号，可作为后续有界观察候选；尚未做该关联的动态验证。
- 文件处理器将同一输出指针入队，出队到 `self+0x64`，调用 `WriteChunks`；随后 `MemoryRelease -> FreeBuffer` 销毁 descriptor 并清空 `image+0x2c`，将 image op 标成11。裸指针只在该代生命周期内有意义。

`NotifyCaptureStart` 只有事件 `0x01010001` 和参数0；`CaptureStartDone` 也回调零参数。接收端回调的 r2/r3 上下文未进入队列。`image+0x10/+0x14` 在本路径是固定通知标签3/0。`RandomKey` 按秒重新播种后取 random，相同种子和调用序列可重复，且未证实透传到文件；它不能代替任务ID。队列复制还会打断上游裸指针身份。

可实现的局部关联应记录真实 `Encode(input,output)`、输出入队/出队、`WriteChunks` 和 `FreeBuffer`，使用进程身份加对象代次，丢记录或遇到歧义就保留未关联。队列关联应观察实际push/front/pop和复制内容身份，不能用时间窗口配对。

尚缺：外部请求／合成快门输入被原厂接受后，对应的 `CProcessParameter/CCapImage` 代次如何映射到某一个 `stCAPTURE_IMG_SET`／`CaptureImgDataSet` 条目。需要核实这条链中的共同字段或显式回调；本次没有证明 `stCAPTURE_IMAGE` 的某个字段携带该身份。

本次到此停止：只读三个相关ELF的已选函数，没有运行相机、改runtime或实现任务关联。`idle`、回调零参数、文件新出现、RandomKey和时间接近均不能补上这条缺口。
''', encoding='utf-8')
    print(json.dumps({'output': str(OUT), 'libraries': {name: len(data['functions'])
                      for name, data in report['libraries'].items()}}, indent=2))


if __name__ == '__main__':
    main()
