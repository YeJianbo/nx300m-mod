"""Summarize bounded, per-call IPCS Open diagnostics; never infer a KDD transition."""
import argparse
import json
import math
from pathlib import Path

PHASES=('total','outer_mutex','udd_mutex','udd_open','device_open','regmap','irq','init')


def ns(pair):
    if not isinstance(pair,list) or len(pair)!=2 or any(type(v) is not int for v in pair):
        raise ValueError('Invalid monotonic timestamp')
    if not 0<=pair[1]<1000000000: raise ValueError('Invalid nanoseconds')
    return pair[0]*1000000000+pair[1]


def quantiles(values):
    values=sorted(values)
    return {label:values[min(len(values)-1,math.ceil(q*len(values))-1)] if values else None
            for label,q in [('p50',.5),('p95',.95),('p99',.99),('max',1)]}


def analyze(rows):
    windows=[r for r in rows if r.get('type')=='ipcs_open_window']
    if len(windows)!=1: raise ValueError('Exactly one open-export snapshot is required')
    window=windows[0]
    complete=window['control_intervals_complete'] is True
    control_rows=window['controls']+[r for r in rows if r.get('type')=='ipcs_open_control']
    controls=[(ns(r['begin']),ns(r['end'])) for r in control_rows]
    if any(a>b for a,b in controls): raise ValueError('Reversed control interval')
    calls,excluded=[],[]
    seen=set()
    for r in rows:
        if r.get('type')!='ipcs_open_call': continue
        key=(r['pid'],r['build'],r['seq'])
        if key in seen: raise ValueError('Duplicate Open call')
        seen.add(key)
        reasons=[]
        if r['committed']!=1: reasons.append('pending')
        p={PHASES[v['phase']]:v for v in r['phases']}
        if len(p)!=len(PHASES): raise ValueError('Incomplete phase schema')
        total=p['total']
        if total['valid']!=3 or total['count']!=1: reasons.append('clock_or_scope_invalid')
        if not complete: reasons.append('control_intervals_incomplete')
        begin,end=ns([r['start_sec'],r['start_nsec']]),ns([r['end_sec'],r['end_nsec']])
        if begin>end: reasons.append('reversed_clock')
        if any(begin<=b and end>=a for a,b in controls): reasons.append('ptrace_control_overlap')
        udd=p['udd_open']
        branch=('first_process_reference' if udd['count']==1 and udd['rc']==0 else
                'existing_process_reference' if udd['count']==1 and udd['rc']==1 else 'unverified_or_error')
        row=dict(seq=r['seq'],tid=r['tid'],role=r['role'],frame=r['frame'],udd_branch=branch,
                 kdd_transition=None,wall_us=total['wall_us'],thread_cpu_us=total['cpu_us'],
                 non_thread_cpu_wall_us=max(0,total['wall_us']-total['cpu_us']),
                 phases={name:{k:v[k] for k in ('wall_us','cpu_us','count','rc','valid')} for name,v in p.items()})
        # UDD includes its mutex and libc open. Only these non-nested phases
        # may be added when examining the enclosing Open call.
        top=('outer_mutex','udd_open','regmap','irq','init')
        row['unattributed_wall_us']=total['wall_us']-sum(p[name]['wall_us'] for name in top)
        if reasons: excluded.append(dict(seq=r['seq'],reasons=reasons))
        else: calls.append(row)
    by_role={}
    for role in (1,2):
        selected=[r for r in calls if r['role']==role]
        by_role[str(role)]={
            'calls':len(selected),'wall_us':quantiles([r['wall_us'] for r in selected]),
            'thread_cpu_us':quantiles([r['thread_cpu_us'] for r in selected]),
            'udd_branches':{branch:sum(r['udd_branch']==branch for r in selected) for branch in
                            ['first_process_reference','existing_process_reference','unverified_or_error']}}
    return dict(schema=1,source='per-call wall + CLOCK_THREAD_CPUTIME_ID; transparent UDD forwarding',
                measurement_source=window.get('measurement_source','unknown'),
                controls_complete=complete,by_role=by_role,excluded=excluded,
                long_calls=[r for r in calls if r['wall_us']>=40000],calls=calls,
                kdd_first_power_verified=False,
                unresolved=['Kernel KDD/PMU and scheduler/IRQ events are required for kernel substage attribution.',
                            'Wall minus thread CPU includes several causes and is not a mutex-wait measurement.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    rows=[json.loads(v) for v in args.input.read_text(encoding='utf-8').splitlines() if v.startswith('{')]
    report=analyze(rows); args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('calls','long_calls')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
