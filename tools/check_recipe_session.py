"""Bounded integration check against the running stock camera process."""
import json
import secrets
import subprocess
import time
from pathlib import Path
import recipe_style as recipe
import sharpness_style


def main():
    evidence = {}
    values = tuple(recipe.catalogue()['portra400']['values'][key] for key in recipe.VALUES)
    try:
        evidence['apply'] = recipe.request(1, values=values)
        assert evidence['apply']['preview_applied']
        evidence['same_value_independent'] = sharpness_style.request(1, level=9)
        evidence['superseded'] = recipe.request()
        assert evidence['superseded']['superseded'] == 1
        evidence['reapply'] = recipe.request(1, values=values)
        assert evidence['reapply']['preview_applied']
        request_id = secrets.randbelow(0x7ffffffe)+1
        wire = ' '.join(map(str, (request_id, int(time.time())+6, 1, 120, 110, 90, 12, 7, 8, 21)))
        result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t',
            str(evidence['reapply']['namespace_pid']), '--mount', 'sh', '-c',
            recipe.TRANSPORT, 'sh', wire, str(request_id), ''], capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', 'replace'))
        evidence['invalid_last_field'] = dict(zip(recipe.FIELDS, map(int, result.stdout.split())))
        assert evidence['invalid_last_field']['bridge_result'] != 0
        evidence['after_invalid'] = recipe.request()
        assert tuple(evidence['after_invalid'][key] for key in recipe.VALUES) == values
        assert evidence['after_invalid']['generation'] == evidence['reapply']['generation']
    finally:
        evidence['reset'] = recipe.request(0)
        path = Path(__file__).resolve().parents[1]/'outputs/metol/recipe-session.json'
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assert evidence['reset']['preview_applied'] and not evidence['reset']['enabled']
    print(json.dumps({'checks': list(evidence), 'preview_applied': True,
        'capture_applied': evidence['reset']['capture_applied'], 'image_verified': False}))


if __name__ == '__main__':
    main()
