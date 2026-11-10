"""Reviewed choices tied to the complete 686-source review snapshot."""
import json
import re
from collections import Counter
import numpy as np
from compare_film_luts_current import ROOT, OUT

RECHECK=OUT/'full-recheck'
SOURCE_REVIEW=json.loads((RECHECK/'inventory.json').read_text(encoding='utf-8'))
SOURCES={e['number']:e for e in SOURCE_REVIEW['entries']}

# Numbers refer to inventory.json, not camera menu positions.
CHOICES=[
 ('松下',[
  (191,'松下 Color Harmony','轻快青蓝与柔和暖肤，保留日常主力。'),
  (190,'松下 Clear-S','比Harmony更清淡、更偏青，保留清透方向。'),
  (189,'松下 City Film','比Harmony、Clear-S更暖、更厚实；恢复都市浓彩。'),
  (188,'松下 糖果色','明亮暖黄褪色、青蓝天空；不是普通提饱和。'),
  (204,'松下 复古夏日','暖肤与偏蓝阴影，比夏日暖风更有色彩密度。'),
  (210,'松下 夏日暖风','浅白肤色、低饱和天空，清淡夏日。'),
  (192,'松下 薄暮苔痕','暖肤、克制绿叶；区别于偏粉的退绿。'),
  (203,'松下 复古旧胶片','较深黑位与暖棕旧片，区别于City Film的浓彩。'),
  (208,'松下 Smoky Color-S','烟灰肤色、青色天空，区别于中性的Eterna。'),
  (199,'松下 千禧回声 冷','沿用原冷版，不改变配方。'),
  (200,'松下 千禧回声 暖','沿用原暖版，不改变配方。'),
  (186,'松下 AgfaPhoto LeBox','保留较强反差的一次性相机风格。'),
 ]),
 ('富士',[
  (170,'富士 Astia','沿用CXO50定版，柔和自然人像。'),
  (19,'富士 Reala Ace','沿用当前版，克制日常色彩。'),
  (55,'富士 400H','沿用既选来源减号版，菜单不另列曝光变体。'),
  (173,'富士 Classic Chrome','沿用CXO50定版，冷静纪实。'),
  (175,'富士 Classic Negative','沿用CXO60定版，较有负片色彩反差。'),
  (178,'富士 Nostalgic Negative','沿用原版，怀旧暖调。'),
  (179,'富士 Pro Neg Std','恢复原版，低饱和、柔和的人像基准。'),
  (125,'富士 Provia','恢复本地原版，中性均衡；区别于风格更强的胶片。'),
  (177,'富士 Eterna','恢复既有CXO40版，低反差电影色彩。'),
  (28,'富士 Eterna Bleach Bypass','低饱和青灰、较强明暗；仍保留彩色。'),
  (7,'富士 Superia 400','沿用CXO35定版，日常胶卷。'),
  (182,'富士 Velvia','恢复数字模拟原版，鲜艳度低于Velvia50。'),
  (147,'富士 Velvia 50','沿用CXO70 B定版，风景浓彩。'),
  (53,'富士 FP-100C Cool','保留蓝黑暗部和暖灯的即时成像冷调。'),
 ]),
 ('宾得',[
  (124,'宾得 风雅','用户必选。'),(117,'宾得 春红','用户必选。'),
  (116,'宾得 夏天','用户必选。'),(112,'宾得 九秋','用户必选。'),
  (94,'宾得 冬野','用户必选。'),(93,'宾得 SATOBI','保留低饱和复古色彩。'),
 ]),
 ('奥林巴斯',[
  (97,'奥林巴斯 柔和','恢复清淡中性柔肤；比Astia灰柔。'),
  (82,'奥林巴斯 Chrome','恢复反差、青蓝与暖色分离。'),
  (107,'奥林巴斯 鲜艳','恢复明亮浓色；比Chrome红黄更强。'),
 ]),
 ('尼康',[
  (167,'尼康 假日','明亮较中性日常，与Clear-S的偏青区别。'),
  (156,'尼康 梦幻','暖色雾感、抬黑，保留梦幻方向。'),
  (158,'尼康 忧郁','偏粉紫雾感，与梦幻暖雾分开。'),
  (380,'尼康 玩具','选择CXO解释，蓝黑暗部、暖色中间调；保留多色。'),
 ]),
 ('柯达',[
  (54,'柯达 Portra 160 NC','保留清淡自然人像负片。'),
  (5,'柯达 Portra 400','沿用CXO50 B定版，温暖柔和。'),
  (92,'柯达 Gold','沿用原版，暖色胶卷。'),
  (4,'柯达 Ektachrome 100','沿用CXO85最新细修定版。'),
  (44,'柯达 Kodachrome 25','淡冷蓝天、克制红色，和Ekt85偏粉方向不同。'),
  (276,'柯达 Ektar 100','蓝天清爽、暖肤更实，比Gold黄底轻。'),
  (56,'柯达 2383','保留浓郁电影色彩，打印映射只留已选一种。'),
 ]),
 ('适马',[(233,'适马 粉蓝','浅青蓝天空与暖肤，区别于Clear-S的冷白肤。')]),
 ('创意与其他',[
  (68,'日出淡彩','沿用现有淡彩。'),(8,'柔和明亮','独立低反差、提明度、低饱和选项。'),
  (50,'Polaroid 669','沿用当前CXO版，避免GitHub版更强青绿。'),
  (1,'青橙','沿用原图40%+CXO60%定版。'),(16,'Pastel','保留较强雾化淡彩。'),
  (13,'秋色 Autumn','绿叶转棕杏，天空保留青蓝；区别于宾得九秋。'),
  (14,'退绿 Desaturated Green','绿色灰橄榄、肤色略粉；不是全局去饱和。'),
  (279,'Adox Color Implosion','粉肤、青天、抬黑褪色；雾化弱于Pastel。'),
  (282,'LomoChrome Metropolis','黄赭暖色、灰蓝天空的都市复古。'),
  (59,'红叶幻想','艺术03原表，植被转红而保留蓝天；独立幻想风格。'),
  (69,'蓝调时刻','蓝色阴影和天空，同时保留暖肤，区别于全幅蓝染。'),
 ]),
]

GROUPS=[(g,[SOURCES[n]['id'] for n,_,_ in rows]) for g,rows in CHOICES]
LABELS={SOURCES[n]['id']:label for _,rows in CHOICES for n,label,_ in rows}
REASONS={SOURCES[n]['id']:reason for _,rows in CHOICES for n,_,reason in rows}
REVIEWED_IMPORTS=[(SOURCES[n]['id'],label,SOURCES[n]['path'],reason)
                  for _,rows in CHOICES for n,label,reason in rows if SOURCES[n]['provider']!='local']

ISSUES={}
def issue(numbers,reason):
    ISSUES.update({n:reason for n in numbers})
issue([20,127,150,155,165], '近黑白、极低彩色，不符合本轮彩色入机方向。')
issue([151,160,163,164,181,226,227,360,455,*range(457,469)],'整幅或双色染色较强，按用户排除黑白染色的偏好不入机。')
issue([32,37,47,48,264,281,283,292,425,586], '本轮日光肤色出现强蓝、黄绿或整幅橙色，不选作入机通用彩色款。')
issue([217,218], '当前移植源蓝天偏紫、肤色偏黄绿；CXO无对应理光源，保留原表待修。')
issue([185,195,202,212], '暖黄或黄绿明显侵入肤色；相较所选松下款，本轮不另设菜单。')
issue([187,194,209,213], '来源依赖非Standard基础风格；当前输入链未匹配，保留完整库。')
issue([57,58,60,61,62,63,64], '整幅粉紫或黄染较强；艺术风格选择保留蓝天和肤色较多的艺术03。')
issue([363,364,375], '补充冷光人像和夜景中整体蓝紫或红棕染色较重，选择蓝调时刻与玩具。')
issue([168], '与CXO玩具为同一风格用途，本轮采用暖色和蓝黑分离更合适的CXO版。')
issue([600], '与400H同属柔和负片用途，冷光肤色更黄；本轮保留已选400H，不另增菜单。')
issue([66,70,73,103,114,115,121], '补充对照主要是轻微冷暖、明暗和灰雾变化；日常微调由机内完成，保留现有清淡款。')
issue(list(range(469,501)), 'PX680/PX70家族在本轮人像和天空有明显黄绿倾向；曝光变体不单列。')
issue(list(range(501,511)), 'Time Zero Expired家族整幅橙黄雾和粉紫明显；曝光变体不单列。')
issue(list(range(675,687)), '打印胶片映射家族；入机保留当前2383，不再加入多种近似clip/map版本。')


def family(label):
    return re.sub(r'\s*[+−–-]+\s*$','',re.sub(r'^CXO\s+','',label)).strip().casefold()


def ledger():
    pages=json.loads((RECHECK/'pages.json').read_text(encoding='utf-8'))
    page_of={n:p['file'] for p in pages for n in p['numbers']}
    from recheck_palette_selection import FOCUS
    focus_of={n:[] for n in SOURCES}
    for i,(_,ns) in enumerate(FOCUS,1):
        for n in ns:focus_of[n].append(f'focused/{i:02d}.jpg')
    selected=[n for _,rows in CHOICES for n,_,_ in rows]
    selected_ids={SOURCES[n]['id']:n for n in selected}
    families={family(SOURCES[n]['label']):n for n in selected}
    vectors=np.load(RECHECK/'scene-signatures.npy')
    references=vectors[np.array(selected)-1]
    records=[]
    for n,e in SOURCES.items():
        distances=np.linalg.norm(references-vectors[n-1],axis=-1).mean(-1)
        nearest=selected[int(np.argmin(distances))]
        item=dict(e,review_page=page_of.get(n),focused_pages=focus_of[n],
                  nearest_selected_number=nearest,nearest_selected_id=SOURCES[nearest]['id'],
                  nearest_selected_mean_delta_E76=float(np.min(distances)))
        if e['id'] in selected_ids:
            status='入机' if e['provider']=='local' or n in selected else '同款来源留档'
            reason=REASONS[e['id']]
        elif e['monochrome'] or e['single_color_tone']:
            status='不入机';reason='黑白或单色染色，按用户本轮彩色偏好排除。'
        elif n in ISSUES:
            status='不入机';reason=ISSUES[n]
        elif 'same_native_table_as' in e:
            status='同表来源留档';reason=f"与编号{e['same_native_table_as']}原生表数值相同，不增重复菜单。"
        elif family(e['label']) in families:
            status='同款变体留档';reason='同款选一个已审版本；曝光与强弱差异交给机内微调。'
        elif e['id'].startswith(('blend-','nx300-fuji','metol-')) or n in [23,24,25,26,27,30,36,38,39,43]:
            status='同类源版留档';reason='对应胶片或品牌风格已有所选版本，保留既定混合配方及原版分工。'
        else:
            status='备选留库'
            reason=(f"与所选{LABELS[SOURCES[nearest]['id']]}作近似风格参考，18场景平均ΔE76={float(np.min(distances)):.2f}；"
                    '本轮未增设菜单，源版与审片保留。距离只用于找相近款，不是质量分数。')
        item.update(decision=status,reason=reason)
        records.append(item)
    report=dict(source_count=len(records),selected_count=len(selected),
                review_scope='468彩色源四场景全覆盖审片；候选追加四场景；全部686源18场景数值签名。',
                variant_policy='同款曝光变体只留一个菜单，机内调整；不同色彩解释分别判断。',
                counts=dict(Counter(e['decision'] for e in records)),entries=records)
    (RECHECK/'decisions.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 全库重新审查与入机选择','',report['review_scope'], '',report['variant_policy'],'',
           '## 入机名单','', '| 分组 | 数量 | 配方 |','|---|---:|---|']
    for g,rows in CHOICES:
        lines.append(f"| {g} | {len(rows)} | "+'、'.join(label for _,label,_ in rows)+' |')
    lines+=['','## 逐项记录','', '| 编号 | 来源 | 名称 | 处理 | 依据 | 审片 |','|---:|---|---|---|---|---|']
    for e in records:
        page=e['review_page']
        preview=f"[图]({page})" if page else '同表或黑白/染色分类'
        preview+=' '+ ' '.join(f'[补图]({p})' for p in e['focused_pages'])
        lines.append(f"| {e['number']} | {e['provider']} | {e['label']} | {e['decision']} | {e['reason']} | {preview} |")
    (RECHECK/'review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    print(json.dumps({k:v for k,v in ledger().items() if k!='entries'},ensure_ascii=False))
