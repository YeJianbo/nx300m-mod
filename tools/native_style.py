"""Shared request and applied-generation tracking for native color stages."""
import secrets
import subprocess
import time

from chroma_style import TRANSPORT as CHROMA_TRANSPORT, FIELDS as CHROMA_FIELDS
from session_stock import inspect_session

FIELDS = ('request_id','bridge_result','level','enabled','generation',
          'capture_generation','preview_generation','capture_modified','preview_modified',
          'capture_error','preview_error','guest_pid')
TRANSPORT = CHROMA_TRANSPORT.replace('nx300m-chroma-style','nx300m-contrast').replace(
    ' '.join(CHROMA_FIELDS),' '.join(FIELDS))
RGB_FIELDS = (*FIELDS[:2], 'red', 'green', 'blue', *FIELDS[3:])


def request_style(name, label, stage, operation=2, *, level=10, wait_applied=True):
    if name not in ('contrast', 'sharpness'):
        raise ValueError('未知原厂参数阶段')
    return _request_stage(name,label,stage,operation,(level,),20,(10,),FIELDS,wait_applied)


def request_source_rgb(operation=2, *, red=100, green=100, blue=100, wait_applied=True):
    return _request_stage('source-rgb','源RGB','ColorD4',operation,(red,green,blue),
                          200,(100,100,100),RGB_FIELDS,wait_applied)


def _request_stage(name,label,stage,operation,requested,maximum,neutral,fields,wait_applied):
    transport = TRANSPORT.replace('nx300m-contrast','nx300m-'+name).replace(
        ' '.join(FIELDS),' '.join(fields))
    if type(operation) is not int or operation not in (0,1,2):
        raise ValueError(f'未知{label}操作')
    if any(type(value) is not int or not 0 <= value <= maximum for value in requested):
        raise ValueError(f'{label}要求0–{maximum}整数档位')
    state=inspect_session(refresh=True)
    if not state.get('running') or not state.get('color_style_ready'):
        raise RuntimeError('当前原厂程序未加载颜色桥')
    ready_key=name.replace('-','_')+'_style_ready'
    if not state.get(ready_key):
        raise RuntimeError(f'当前原厂{stage}尚未完成首次调用，{label}控制未就绪')
    namespace=state['namespace_pid']

    def exchange(op):
        request_id=secrets.randbelow(0x7ffffffe)+1
        wire=' '.join(map(str,(request_id,int(time.time())+6,op,*requested)))
        result=subprocess.run(['wsl.exe','-u','root','-e','nsenter','-t',str(namespace),'--mount',
            'sh','-c',transport,'sh',wire,str(request_id),''],capture_output=True,timeout=15)
        if result.returncode:
            raise RuntimeError(f'{label}请求退出{result.returncode}: {result.stderr.decode("utf-8","replace").strip()}')
        values=result.stdout.decode('ascii').split()
        if len(values)!=len(fields): raise RuntimeError(f'{label}响应长度无效')
        response=dict(zip(fields,map(int,values)))
        if response['request_id']!=request_id or response['guest_pid']<=0:
            raise RuntimeError(f'{label}响应身份无效')
        if response['bridge_result']:
            raise RuntimeError(f'{label}请求被拒绝: {response}')
        response['preview_applied']=(response['preview_generation']==response['generation'] and
            response['preview_modified']==response['enabled'] and response['preview_error']==0)
        response['capture_applied']=(response['capture_generation']==response['generation'] and
            response['capture_modified']==response['enabled'] and response['capture_error']==0)
        return response

    response=exchange(operation)
    configured_generation=response['generation']
    deadline=time.monotonic()+6
    while operation!=2 and wait_applied and not response['preview_applied'] and time.monotonic()<deadline:
        if response['preview_generation']==configured_generation and response['preview_error']:
            raise RuntimeError(f'原厂{stage}更新已到达，但{label}算子返回错误: {response}')
        time.sleep(0.1)
        response=exchange(2)
        if response['generation']!=configured_generation:
            raise RuntimeError(f'等待{stage}更新时{label}被其他请求修改')
    current=inspect_session(refresh=True)
    if not current.get('running') or current.get('namespace_pid')!=namespace or not current.get(ready_key):
        raise RuntimeError(f'{label}请求期间原厂会话变化')
    actual=tuple(response[key] for key in fields[2:2+len(requested)])
    if operation!=2 and (response['enabled']!=int(operation==1) or actual!=(requested if operation else neutral)):
        raise RuntimeError(f'{label}配置读回不一致: {response}')
    response.update(namespace_pid=namespace,persistent=False,image_verified=False,
        capture_available=response['capture_generation']!=0)
    return response

