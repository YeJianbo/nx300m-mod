"""Control source RGB column scaling before native PSS/ColorTone composition."""
import argparse
import json
from native_style import request_source_rgb as request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--set',nargs=3,type=int,metavar=('R','G','B'))
    action.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    values=args.set if args.set is not None else (100,100,100)
    result=request(1 if args.set is not None else 0 if args.reset else 2,
                   red=values[0],green=values[1],blue=values[2])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if (args.set is not None or args.reset) and not result['preview_applied']:
        raise SystemExit('配置已排队，但尚未观察到对应的原厂色彩更新')


if __name__=='__main__':
    main()
