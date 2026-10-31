"""Two targeted state checks for strict batch chroma and real tone ownership."""
import json
from pathlib import Path
import shlex
import subprocess
from build_ycc_probe import wsl_path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/metol'


def main():
    base=wsl_path(ROOT)
    script=f'''set -eu
cd {shlex.quote(base)}
gcc -std=c99 -O2 -Wall -Wextra -Werror -Wno-unused-function \
 runtime/recipe_runtime_state_probe.c -o outputs/metol/recipe-runtime-state-probe.host
'''
    build=subprocess.run(['wsl.exe','-e','sh','-c',script],capture_output=True,timeout=30)
    (OUT/'recipe-runtime-state-build.log').write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError(f'Build exit{build.returncode}: {build.stderr.decode("utf-8","replace")}')
    run=subprocess.run(['wsl.exe','-e',base+'/outputs/metol/recipe-runtime-state-probe.host'],capture_output=True,timeout=15)
    stdout,stderr=(value.decode('utf-8','replace') for value in (run.stdout,run.stderr))
    (OUT/'recipe-runtime-state-probe.stdout.log').write_text(stdout,encoding='utf-8')
    (OUT/'recipe-runtime-state-probe.stderr.log').write_text(stderr,encoding='utf-8')
    cases=[dict(field.split('=',1) for field in line.split()[1:]) for line in stdout.splitlines()
           if line.startswith('RECIPE_STATE_CASE ')]
    passed=run.returncode==0 and len(cases)==2 and all(case['failures']=='0' for case in cases)
    report={'build_exit_code':build.returncode,'exit_code':run.returncode,'passed':passed,'cases':cases,
            'production_headers':['runtime/contrast_style.h','runtime/chroma_style.h'],
            'scope':'Host-native deterministic state fixture with explicit ApplyLut, tone setter, mode and selector SDK mocks. Checks only the two reviewed integration defects and pair-reset failure status.',
            'not_verified':'No original ARM arithmetic, original firmware consumer, actual ISP registers, wire25 exchange or pixel output. Parent owns live batch preflight/superseded/reset checks.',
            'production_files_modified':False,'running_session_changed':False,'stdout':stdout,'stderr':stderr}
    (OUT/'recipe-runtime-state-probe.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not passed: raise SystemExit(run.returncode or 1)


if __name__=='__main__': main()
