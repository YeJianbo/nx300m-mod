"""Sustained X11 wheel backlog: require intermediate paints, not just fewer."""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import probe_menu_coalesce_vm as probe

out = ROOT / 'outputs/review/2026-11-06-night/draw-sustained-v3'
# The same finite backlog reaches both binaries; no hardware or engine writes.
probe.BURST_KEYS = ' '.join(['XF86ScrollDown'] * 800)
probe.PROBE = probe.PROBE.replace('set -x', '').replace('[ "$volley" -lt 6 ]', '[ "$volley" -lt 1 ]').replace('--lifetime-ms 9000', '--lifetime-ms 12000')
sys.argv = [__file__, '--output-dir', str(out)]
probe.main()
observations = {}
for variant in ('before', 'current'):
    text = (out / f'coalesce-{variant}.log').read_text()
    frames = [dict(dequeued=int(a), submitted=int(b), events=int(c), painted=int(d))
              for a,b,c,d in re.findall(r'PANEL_FRAME pid=\d+ dequeued_ms=(-?\d+) submitted_ms=(-?\d+) events=(\d+) painted=(\d+) exposed=\d+',text)]
    full = [row for row in frames if row['events'] == 64]
    observations[variant] = dict(full_input_batches=len(full),
                                paints_during_full_batches=sum(row['painted'] for row in full),
                                full_batch_span_ms=full[-1]['submitted'] - full[0]['submitted'] if full else None)
report = dict(diagnostic_completed=all(value['full_input_batches'] >= 5 for value in observations.values()),
              contracts_passed=observations['current']['paints_during_full_batches'] > 0,
              scope='Same finite 800-wheel backlog on real ARM X11 UI; software only; shell xtrace off',
              observations=observations)
(out/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
