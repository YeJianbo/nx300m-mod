"""Keep the Still RAM image and every readiness consumer on one code identity."""
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT/'outputs/deployment/lut-live-loader'


def identity():
    blob = (BUILD/'lut_ram_blob.bin').read_bytes()
    words = struct.unpack('<16I', blob[:64])
    generated = (ROOT/'runtime/lut_ram_blob_build.h').read_text(encoding='ascii')
    tag = int(re.search(r'#define NX_LUT_BLOB_BUILD (\d+)U', generated)[1])
    if (words[:2] != (0x4e584c42, 1) or words[2] != len(blob) or
            words[9] != 196 or words[13] != tag):
        raise ValueError('Still LUT blob/header differ; build_lut_ram_blob.py must run before its consumers')
    return dict(build=tag, config_abi_bytes=196, blob_sha256=hashlib.sha256(blob).hexdigest())


def record(binary, path):
    data = identity()
    data.update(binary=str(binary.relative_to(ROOT)), sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    path.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')


def require(binary, path):
    data = json.loads(path.read_text(encoding='utf-8'))
    expected = identity()
    if (any(data.get(key) != value for key, value in expected.items()) or
            data.get('sha256') != hashlib.sha256(binary.read_bytes()).hexdigest()):
        raise ValueError('Rebuild the matching Still LUT consumer: '+str(binary))
    return data
