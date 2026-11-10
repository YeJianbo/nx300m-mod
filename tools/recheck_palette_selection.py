"""Focused visual comparisons and source-by-source curation ledger."""
from pathlib import Path
import json
import sys
import numpy as np
from compare_film_luts_current import ROOT, OUT, inputs, grid, write_json
from nx300m_lut_core import parse_lut_bytes, apply_lut

sys.stdout.reconfigure(encoding='utf-8')
DEST = OUT/'full-recheck'
FOCUS = [
    ('城市与浓彩', [189,191,190,82,107,182,147]),
    ('柔和与电影', [97,8,170,179,177,208,28]),
    ('暖调夏日', [188,204,210,167,233,190,600]),
    ('复古与植被', [13,14,192,203,112,279,282]),
    ('胶片色彩', [44,4,276,92,5,54,55]),
    ('风格化', [156,158,363,364,375,380,59]),
    ('日系轻调', [66,69,70,73,103,114,115,121]),
]


def focused():
    entries={e['number']:e for e in json.loads((DEST/'inventory.json').read_text(encoding='utf-8'))['entries']}
    photos=inputs()
    scenes=[2,5,11,13]
    for index,(title,numbers) in enumerate(FOCUS,1):
        rows=[('原图',[(photos[i][0],photos[i][1]) for i in scenes])]
        for n in numbers:
            e=entries[n]; path=ROOT/e['path']
            lut=parse_lut_bytes(path.read_bytes(),path.name)
            rows.append((f"{n:03d} · {e['label']}",[('',apply_lut(photos[i][1],lut)) for i in scenes]))
        grid(title+' · 补充审片','冷光人像 / 夜景 / F盘场景；同底图、100%配方。',rows,
             DEST/'focused'/f'{index:02d}.jpg',350,220)
        print(title,flush=True)


if __name__=='__main__':
    focused()
