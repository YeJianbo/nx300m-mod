"""Configure source Contrast at the original Gamma stage (transient session)."""
import argparse
import json
from native_style import FIELDS, TRANSPORT, request_style


def request(operation=2, *, level=10, wait_applied=True):
    result = request_style('contrast', '对比度', 'Gamma', operation,
                           level=level, wait_applied=wait_applied)
    result['preview_quantization'] = 'nearest 8-bit to 10-bit, source interpolation and upper cap, nearest back to 8-bit'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--set',type=int,metavar='LEVEL')
    action.add_argument('--reset',action='store_true')
    args=parser.parse_args()
    result=request(1 if args.set is not None else 0 if args.reset else 2,
                   level=args.set if args.set is not None else 10)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if (args.set is not None or args.reset) and not result['preview_applied']:
        raise SystemExit('配置已排队，但尚未观察到对应的原厂Gamma更新')


if __name__=='__main__':
    main()
