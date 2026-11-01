"""Audit stock IPCM tonal APIs and parameter arithmetic without a camera."""
import hashlib
import json
from pathlib import Path

from inspect_gamma_hook import Stock
from inspect_recipe_tables import disassemble

ROOT = Path(__file__).resolve().parents[1]


def inspect():
    rootfs = Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    iq = Stock(rootfs/'usr/lib/libiq-fw-slpcam.so')
    udd = Stock(rootfs/'usr/lib/libudd.so.3.1')
    records, listings = [], []
    for stock, related in ((udd, lambda n: n in (
        'ipcm_tone_lut','ipcm_ylut','ipcm_chroma_lut','ipcm_color_correction_param',
        'ipcm_rgb2ycc_param','ipcm_ycc2rgb_param')),
        (iq, lambda n: any(c in n for c in ('CIpcmGammaD4','CIpcmYLutD4','CIpcmColorD4','CIpcmChromaD4','CIpcLiveviewParamD4'))
         and any(m in n for m in ('SetParam','C1Ev','SetRgb2Ycc','GetIndex')))):
        names = [n for n,s in stock.symbols.items() if related(n) and s['st_info']['type']=='STT_FUNC']
        listing, functions = disassemble(stock.elf,stock.symbols,names)
        listings.append('# '+stock.path.name+'\n'+listing)
        records.append({'library':stock.path.name,'sha256':hashlib.sha256(stock.path.read_bytes()).hexdigest(),'functions':functions})
    # Bind each copy size to an actual reviewed instruction, rather than a
    # function-name interpretation. The destinations are UDD context RAM.
    evidence = [(udd,0x4b834,'mov','r2, #0x800'),(udd,0x4b3a4,'mov','r2, #0x100'),
                (udd,0x4b570,'mov','r2, #0x40'),(udd,0x4b594,'mov','r2, #0x40')]
    for stock,address,mnemonic,operands in evidence:
        instruction = stock.instruction(address)
        if (instruction.mnemonic,instruction.op_str)!=(mnemonic,operands):
            raise ValueError(f'Unreviewed instruction {address:#x}')
    tables = {name:iq.table(name,width) for name,width in (
        ('_ZN12CIpcmGammaD413m_iBaseGammaXE',15),('_ZN12CIpcmGammaD413m_iBaseGammaYE',15),
        ('_ZN11CIpcmYLutD412m_iBaseYLutXE',7),('_ZN11CIpcmYLutD412m_iBaseYLutYE',7),
        ('_ZN12CIpcmColorD410m_iRgb2YuvE',9))}
    result = {'scope':'Original ELF declarations and instruction/data flow; no ISP pixels or camera writes',
        'libraries':records,'tables':tables,
        'confirmed':{
            'tone':{'entry':'0x4b7d4','bytes':2048,'type':'uint8','copy':'0x4b834..0x4b838','context_offset':'0x800',
                    'api':'one pointer; no per-RGB channel selector exposed by this setter'},
            'ylut':{'entry':'0x4b344','bytes':256,'type':'uint8','copy':'0x4b3a4..0x4b3a8','context_offset':'0x500',
                    'iq_setparam':'0x340914','iq_submission':'0x340a14',
                    'liveview_constructor':'0x3407bc','liveview_list_add':'0x3407c8',
                    'role':'SDK calls this Y Look Up Table; native IQ has static and adaptive Y-Gamma branches'},
            'chroma_lut':{'bytes_each':64,'selectors':['orange','cyan'],
                          'not_demonstrated':'Per-luminance tint or independent RGB curve'},
            'bright_dark':{'api':'ipcm_rgb2ycc_param selector0/2',
                           'sdk_role':'bright/dark chroma suppression, not evidence for additive split toning'},
            'profile_strength':'rounded(identity*(100-strength)+value*strength,100); matches Python whole-sum ties-away',
            'matrix_composition':'profile Q12 matrix left-multiplies fresh stock matrix; each dot product is rounded /4096; signed native word range -1024..1023; entire matrix falls back if any result is out of range',
            'tone_composition':'creative uint8 T indexes the fresh stock uint8 Tone output; the RAM hook does not add another base Gamma'},
        'unverified':[
            'Physical ordering of CCM, Tone, YCC, YLUT, PCSE and display transforms',
            'Liveview YLUT enable state in each shooting mode; parameter submission does not establish active processing',
            'Native pixel colour space, chroma centre/range and channel order',
            'Pixel interpolation, internal arithmetic precision, clipping and rounding',
            'Actual dynamic stock CCM and Tone under exposure/WB changes',
            'Meaning of the two chroma LUT coordinates and their effect on gray'],
        'findings':[
            {'priority':1,'issue':'Software proxy places creative CCM in display-code coordinates; actual before/after-Gamma relation is unverified.',
             'action':'Keep code, sRGB-linear and stock-knot sensitivity models explicit; do not infer a calibrated order from call order.'},
            {'priority':2,'issue':'Q12 payload evaluation omits composition rounding to native parameter words and whole-matrix fallback.',
             'action':'Evaluate old/new profiles using the same native-parameter reference model; separate this from pixel emulation.'},
            {'priority':3,'issue':'Mean RGB error hides opposite shadow/gray cast and luma/chroma trades.',
             'action':'Report signed gray opponents, luminance bins, hue/chroma statistics, float-fit and quantization errors.'},
            {'priority':4,'issue':'Shared Tone and matrices do not supply independent RGB curves through the reviewed APIs.',
             'action':'Test constrained fits; treat YLUT as a separate research path until its active pixel behaviour is verified.'}]}
    return result,'\n'.join(listings)


def main():
    result,listing=inspect()
    output=ROOT/'outputs/metol/ipcm-tonal'
    output.with_suffix('.asm').write_text(listing,encoding='utf-8')
    output.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output.with_suffix('.json')),'confirmed':result['confirmed'],'unverified':result['unverified']},ensure_ascii=False))


if __name__=='__main__':main()
