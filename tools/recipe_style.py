"""Submit all seven source recipe values to the single native batch bridge."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import time

from chroma_style import TRANSPORT as CHROMA_TRANSPORT, FIELDS as CHROMA_FIELDS
from session_stock import inspect_session

ROOT=Path(__file__).resolve().parents[1]
VALUES=('red','green','blue','hue','saturation','sharpness','contrast')
NEUTRAL=(100,100,100,10,10,10,10)
STAGES=('rgb','chroma','sharpness','contrast')
FIELDS=('request_id','bridge_result','generation','enabled',*VALUES,
        'preview_applied_mask','capture_applied_mask','preview_available_mask','capture_available_mask',
        *(f'preview_{stage}_error' for stage in STAGES),
        *(f'capture_{stage}_error' for stage in STAGES),'superseded','guest_pid')
TRANSPORT=CHROMA_TRANSPORT.replace('nx300m-chroma-style','nx300m-recipe').replace(
    ' '.join(CHROMA_FIELDS),' '.join(FIELDS))


def catalogue():
    result={}
    path=ROOT/'outputs/metol/recipes.txt'
    for number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
        if not line or line.startswith('#'): continue
        parts=line.split('|')
        if len(parts)!=9 or not parts[0] or not parts[1] or parts[0] in result:
            raise ValueError(f'配方表第{number}行格式或ID无效')
        values=tuple(map(int,parts[2:]))
        validate(values)
        result[parts[0]]={'id':parts[0],'label':parts[1],'values':dict(zip(VALUES,values))}
    return result


def validate(values):
    if len(values)!=7 or any(type(value) is not int or not 0<=value<=(200 if i<3 else 20)
                            for i,value in enumerate(values)):
        raise ValueError('配方要求7个整数：RGB为0–200，其余四项为0–20')


def describe(response):
    for path in ('preview','capture'):
        response[path+'_applied']=response[path+'_applied_mask']==15 and not response['superseded']
        response[path+'_stages']={stage:{
            'available':bool(response[path+'_available_mask']&(1<<i)),
            'applied':bool(response[path+'_applied_mask']&(1<<i)),
            'error':response[f'{path}_{stage}_error'],
        } for i,stage in enumerate(STAGES)}
    response.update(persistent=False,image_verified=False)
    return response


def request(operation=2, *, values=NEUTRAL, wait_applied=True):
    if type(operation) is not int or operation not in (0,1,2): raise ValueError('未知配方操作')
    values=tuple(values);validate(values)
    state=inspect_session(refresh=True)
    if not state.get('running') or not state.get('recipe_style_ready'):
        raise RuntimeError('当前原厂会话尚未就绪七维配方桥')
    namespace=state['namespace_pid']

    def exchange(op):
        request_id=secrets.randbelow(0x7ffffffe)+1
        wire=' '.join(map(str,(request_id,int(time.time())+6,op,*values)))
        result=subprocess.run(['wsl.exe','-u','root','-e','nsenter','-t',str(namespace),'--mount',
            'sh','-c',TRANSPORT,'sh',wire,str(request_id),''],capture_output=True,timeout=15)
        if result.returncode:
            raise RuntimeError(f'配方请求退出{result.returncode}: {result.stderr.decode("utf-8","replace").strip()}')
        raw=result.stdout.decode('ascii').split()
        if len(raw)!=len(FIELDS): raise RuntimeError('配方响应字段数量无效')
        response=dict(zip(FIELDS,map(int,raw)))
        if response['request_id']!=request_id or response['guest_pid']<=0:
            raise RuntimeError('配方响应身份无效')
        if response['bridge_result']:
            raise RuntimeError(f'配方提交返回错误，当前读回：{describe(response)}')
        if response['enabled'] not in (0,1) or response['superseded'] not in (0,1) or response['generation']<=0:
            raise RuntimeError('配方响应状态无效')
        for field in ('preview_applied_mask','capture_applied_mask','preview_available_mask','capture_available_mask'):
            if not 0<=response[field]<=15: raise RuntimeError('配方响应阶段掩码无效')
        validate(tuple(response[key] for key in VALUES))
        return describe(response)

    response=exchange(operation)
    generation=response['generation']
    deadline=time.monotonic()+8
    while operation!=2 and wait_applied and not response['preview_applied'] and time.monotonic()<deadline:
        if response['superseded']: raise RuntimeError(f'配方已被单项控制修改：{response}')
        time.sleep(.1)
        response=exchange(2)
        if response['generation']!=generation: raise RuntimeError('等待应用时配方已被其他请求替换')
    current=inspect_session(refresh=True)
    if not current.get('running') or current.get('namespace_pid')!=namespace or not current.get('recipe_style_ready'):
        raise RuntimeError('配方请求期间原厂会话变化')
    if operation!=2 and (response['enabled']!=int(operation==1) or
                         tuple(response[key] for key in VALUES)!=(values if operation else NEUTRAL)):
        raise RuntimeError(f'配方读回与提交不符：{response}')
    response['namespace_pid']=namespace
    return response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--list',action='store_true')
    action.add_argument('--apply',metavar='ID')
    action.add_argument('--set',nargs=7,type=int,metavar=('R','G','B','H','SAT','SHARP','CONTRAST'))
    action.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    if args.list:
        print(json.dumps(list(catalogue().values()),ensure_ascii=False,indent=2));return
    values=NEUTRAL
    if args.apply:
        records=catalogue()
        if args.apply not in records: parser.error('配方ID不存在；使用 --list 查看')
        values=tuple(records[args.apply]['values'][key] for key in VALUES)
    elif args.set is not None: values=tuple(args.set)
    operation=1 if args.apply or args.set is not None else 0 if args.reset else 2
    response=request(operation,values=values)
    print(json.dumps(response,ensure_ascii=False,indent=2))
    if operation!=2 and not response['preview_applied']:
        raise SystemExit('配方已提交，但各阶段尚未全部应用；请查看阶段状态及错误')


if __name__=='__main__':
    main()
