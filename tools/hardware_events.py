"""Write one explicit lab hardware event to the private camera session."""
import argparse
import json
import subprocess

from session_stock import inspect_session

GUEST = '/opt/nx300m-lab/stock-1.15'
PP_EVENT_MASK = 0x04e803fd


def send_event(event, value):
    if event == 'ssif' and (type(value) is not int or not 0 <= value < 13):
        raise ValueError('SSIF 事件编号必须是 0 至 12 的整数')
    if event == 'pp' and (type(value) is not int or value <= 0 or value & ~PP_EVENT_MASK):
        raise ValueError('PP 状态位必须是非零的已定义中断位组合')
    if event == 'pwm' and (type(value) is not int or not 0 <= value < 18):
        raise ValueError('PWM 输入编码为 source*2+level，source 0…8，level 0/1')
    if event not in ('ssif', 'pp', 'pwm'): raise ValueError('未知硬件事件接口')
    state = inspect_session()
    if not state['running']: raise RuntimeError('原厂相机程序未运行')
    pid = state['namespace_pid']
    # A FIFO with no reader returns ENXIO. Never block the caller waiting for a
    # backend that has not loaded, and never turn an absent pipe into a file.
    script = '''import os,stat,sys
path=sys.argv[1]
if not stat.S_ISFIFO(os.stat(path).st_mode): raise RuntimeError("Hardware event path is not a FIFO")
fd=os.open(path, os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
try:
    value=(sys.argv[2]+"\\n").encode("ascii")
    if os.write(fd,value)!=len(value): raise RuntimeError("Short hardware event write")
finally: os.close(fd)
'''
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid), '--mount',
                             'python3', '-c', script, GUEST+f'/var/run/nx300m-{event}-events', str(value)],
                            capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace').strip())
    key = {'ssif': 'selection', 'pp': 'status_mask', 'pwm': 'source_level_code'}[event]
    return {'namespace_pid': pid, 'event': event, key: value,
            'input_written': True, 'callback_execution_verified': False,
            'sensor_frame_generated': False, 'image_verified': False}


def send_ssif(selection): return send_event('ssif', selection)
def send_pp(status_mask): return send_event('pp', status_mask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event', choices=('ssif', 'pp', 'pwm'))
    parser.add_argument('value', type=lambda text: int(text, 0), help='SSIF 编号 0…12；PP 状态位；PWM source*2+level（0…17）')
    args = parser.parse_args()
    print(json.dumps(send_event(args.event, args.value), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
