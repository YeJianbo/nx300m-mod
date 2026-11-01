"""Manage custom .cube LUTs and inspect installed Lightroom resources."""
import argparse
import json
import os
from pathlib import Path

from adobe_profiles import DcpProfile, xmp_metadata
from look_library import LookLibrary

PROJECT = Path(__file__).resolve().parents[1]

def inventory():
    user = Path(os.environ['APPDATA'])/'Adobe/CameraRaw'
    resources = Path('./local/lightroom/Resources/CameraProfiles')
    roots = [user/'Settings', user/'CameraProfiles', resources]
    profiles, errors = [], []
    for root in roots:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in ('.dcp', '.xmp'): continue
            try:
                info = DcpProfile(path).metadata() if path.suffix.lower() == '.dcp' else xmp_metadata(path)
                info['location'] = 'user' if path.is_relative_to(user) else 'lightroom-resources'
                profiles.append(info)
            except (OSError, ValueError) as exc: errors.append({'path': str(path), 'error': str(exc)})
    baseline_path = resources/'Adobe Standard/Samsung NX300 Adobe Standard.dcp'
    if baseline_path.exists():
        baseline = DcpProfile(baseline_path)
        for info in profiles:
            if info['kind'] == 'dcp' and info.get('camera_model') == 'Samsung NX300':
                info['nx300_adobe_matrix_comparison'] = {name: info.get(name) == baseline.get(tag) for tag, name in ((50721, 'color_matrix_1'), (50722, 'color_matrix_2'), (50964, 'forward_matrix_1'), (50965, 'forward_matrix_2'))}
    return {'profiles': profiles, 'errors': errors, 'counts': {kind: sum(p['kind'] == kind for p in profiles) for kind in ('dcp', 'xmp')}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    rename = commands.add_parser('rename', help='修改显示名称，保留风格 ID 和颜色表')
    rename.add_argument('id'); rename.add_argument('label')
    commands.add_parser('sync', help='同步到运行中的模拟器，保留当前选择')
    add = commands.add_parser('add')
    add.add_argument('cube', type=Path); add.add_argument('id'); add.add_argument('--label'); add.add_argument('--input-space', default='unspecified')
    build = commands.add_parser('build'); build.add_argument('id')
    preview = commands.add_parser('preview', help='Compare source, master LUT and reduced D4 table offline')
    preview.add_argument('id'); preview.add_argument('image', type=Path); preview.add_argument('output', type=Path)
    preview.add_argument('--amount', type=float, default=1.)
    preview.add_argument('--max-edge', type=int, default=900)
    inspect = commands.add_parser('inspect'); inspect.add_argument('profile', type=Path)
    dcp = commands.add_parser('add-dcp-look'); dcp.add_argument('profile', type=Path); dcp.add_argument('id'); dcp.add_argument('--label'); dcp.add_argument('--size', type=int, default=33)
    stage = commands.add_parser('export-dcp-style', help='导出曝光、LookTable、曲线阶段；输入须为未渲染的线性 ProPhoto')
    stage.add_argument('profile', type=Path); stage.add_argument('output', type=Path)
    stage.add_argument('--size', type=int, default=33)
    raw = commands.add_parser('render-raw-dcp', help='用NX300完整DCP显影原始SRW或Bayer DNG；输出带ICC的sRGB PNG')
    raw.add_argument('raw', type=Path); raw.add_argument('output', type=Path)
    selection = raw.add_mutually_exclusive_group(required=True)
    selection.add_argument('--style', choices=('ricoh-positive-exact', 'ricoh-negative-exact',
        'ricoh-positive-blue-adjusted', 'ricoh-negative-blue-adjusted'))
    selection.add_argument('--profile', type=Path)
    bayer = commands.add_parser('bayer-to-dng', help='将实测Bayer包与同源RAW校准写入DNG，保持原始样本')
    bayer.add_argument('packet', type=Path); bayer.add_argument('reference_raw', type=Path)
    bayer.add_argument('output', type=Path)
    bayer.add_argument('--crop', nargs=4, required=True, type=int, metavar=('X','Y','WIDTH','HEIGHT'))
    bayer.add_argument('--profile', type=Path)
    fit = commands.add_parser('fit-camera-look', help='以同一曝光的机内JPEG和完整RAW显影参考拟合独立sRGB LUT候选')
    fit.add_argument('manifest', type=Path); fit.add_argument('output_dir', type=Path)
    slot = commands.add_parser('add-metol-slot'); slot.add_argument('slot', type=Path); slot.add_argument('id'); slot.add_argument('--label'); slot.add_argument('--input-space', default='unspecified')
    commands.add_parser('inventory')
    args = parser.parse_args(); library = LookLibrary()
    if args.command == 'list': result = library.list()
    elif args.command == 'rename': result = library.rename(args.id, args.label)
    elif args.command == 'sync':
        from metol_stock import sync_looks
        result = sync_looks()
    elif args.command == 'add': result = library.add_cube(args.cube, args.id, label=args.label, input_space=args.input_space)
    elif args.command == 'build': result = library.build(args.id)
    elif args.command == 'preview': result = library.preview(args.id, args.image, args.output, amount=args.amount, max_edge=args.max_edge)
    elif args.command == 'add-dcp-look': result = library.add_dcp_look(args.profile, args.id, label=args.label, size=args.size)
    elif args.command == 'export-dcp-style':
        from dcp_style_export import export
        result = export(args.profile, args.output, args.size)
    elif args.command == 'render-raw-dcp':
        from convert_camera_look import selected_profile, render_raw
        result = render_raw(args.raw, args.profile or selected_profile(args.style), args.output)
    elif args.command == 'bayer-to-dng':
        from bayer_dcp import packet_to_dng
        from convert_camera_look import selected_profile
        result = packet_to_dng(args.packet, args.reference_raw, args.output,
            args.profile or selected_profile('ricoh-positive-exact'), crop=args.crop)
    elif args.command == 'fit-camera-look':
        from fit_camera_look import run
        result = run(args.manifest, args.output_dir)
    elif args.command == 'add-metol-slot': result = library.add_metol_slot(args.slot, args.id, label=args.label, input_space=args.input_space)
    elif args.command == 'inspect': result = DcpProfile(args.profile).metadata() if args.profile.suffix.lower() == '.dcp' else xmp_metadata(args.profile)
    else:
        result = inventory()
        output = PROJECT/'outputs/looks/local-profiles.json'; output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        result = {'output': str(output), 'counts': result['counts'], 'errors': result['errors']}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
