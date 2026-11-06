"""Shorten existing LUT display names without changing ids or native tables."""
import argparse
import json
from pathlib import Path
import re

from look_library import LookLibrary

EXACT = {
    'Hard Mono': '硬调黑白', 'GR Neg': '理光 负片', 'GR Pos': '理光 正片',
    'Pana Classic Neo': '松下 经典 Neo', 'Pentax Bright': '宾得 鲜明',
    'Sony FL': '索尼 FL',
    '富士 CC 经典正片': '富士 经典正片',
    '富士 NC／CN 经典负片': '富士 经典负片',
    '富士 ETERNA': '富士 Eterna 电影', '富士 ETERNA 银漂': '富士 银漂',
    '富士 PRO Neg Std': '富士 Pro 标准', '富士 Pro Neg Std': '富士 Pro Std',
    '富士 PROVIA 标准': '富士 Provia', '富士 ASTIA 柔和': '富士 Astia 柔和',
    '富士 ACROS 黑白': '富士 Acros', '富士 Velvia 浓彩': '富士 Velvia 浓彩',
    '奥林巴斯 Vivid': '奥巴 鲜艳', '奥林巴斯 Muted': '奥巴 柔和',
    '奥林巴斯 Chrome': '奥巴 Chrome',
    '宾得 Natural': '宾得 自然', '宾得 Gold': '宾得 金色',
    '宾得 银盐漂白': '宾得 银漂', '宾得 冬野 Fuyuno': '宾得 冬野',
    '宾得 九秋 Kyushu': '宾得 九秋', '宾得 夏天 Katen': '宾得 夏天',
    '宾得 春红 Harubeni': '宾得 春红',
    '松下 Cinelike D': '松下 电影 D', '松下 L Monochrome': '松下 L 黑白',
    '松下 L Classic Neo': '松下 L 经典',
    '尼康 Warm': '尼康 暖色', '佳能 Clean': '佳能 清透',
    '索尼 Clean': '索尼 清透', '徕卡 Standard': '徕卡 标准',
    '电影 哑光': '电影 哑光', '电影 青色阴影': '电影 青影',
}
NIKON = dict(Binary='二值黑白', Bleach='银漂', Blue='蓝调', Carbon='炭黑',
    Charcoal='木炭', Denim='牛仔蓝', Dramatic='戏剧', Dream='梦幻',
    Graphite='石墨', Melancholic='忧郁', Morning='晨光', Pink='粉色',
    Pop='鲜艳', Pure='纯净', Red='红调', Sepia='棕调', Silence='静谧',
    Somber='暗调', Sunday='假日', Toy='玩具')
SIGMA = {'Cinema':'电影', 'Forest Green':'森林绿', 'FOV Classic Blue':'经典蓝',
    'FOV Classic Yellow':'经典黄', 'Powder Blue':'粉蓝', 'Sunset Red':'落日红',
    'Teal and Orange':'青橙', 'Warm Gold':'暖金'}

def short_label(label):
    label = re.sub(r'^(?:CR|LR|Metol)\s+', '', label.strip())
    label = re.sub(r'^清新\s+', '', label)
    label = re.sub(r'^Artistic\s+(\d+)$', r'艺术 \1', label)
    label = re.sub(r'^Modern\s+(\d+)$', r'现代 \1', label)
    label = re.sub(r'^Vintage\s+(\d+)$', r'复古 \1', label)
    label = EXACT.get(label, label)
    if label.startswith('尼康 '):
        name=label.removeprefix('尼康 ')
        label='尼康 '+NIKON.get(name,name)
    if label.startswith('适马 '):
        name=label.removeprefix('适马 ')
        label='适马 '+SIGMA.get(name,name.replace('Duotone ', '双色 '))
    return ' '.join(label.split())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args=parser.parse_args()
    library=LookLibrary()
    entries=[row for row in library.list() if not row['id'].startswith(('native-','test-'))]
    changes=[]; names={}
    for row in entries:
        name=short_label(row['label'])
        if name in names:
            raise ValueError('Ambiguous display name: '+name+' / '+names[name]+' / '+row['id'])
        names[name]=row['id']
        if name!=row['label']:changes.append(dict(id=row['id'],before=row['label'],after=name))
    if args.apply:
        for item in changes:library.rename(item['id'],item['after'])
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(dict(applied=args.apply,changed=len(changes),changes=changes),
        ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(changes)} display names '+('updated' if args.apply else 'pending'))

if __name__=='__main__':main()
