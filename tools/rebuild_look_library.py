"""Rebuild stale camera LUT tables in parallel with per-look atomic writes."""
from __future__ import annotations

import argparse
from concurrent.futures import as_completed, ProcessPoolExecutor
import json
import os

from cube_to_d4 import METHODS
from look_library import LookLibrary


def rebuild_one(item: tuple[str, str]) -> tuple[str, str]:
    key, method = item
    metadata = LookLibrary().ensure_compiled(key, method=method)
    return key, metadata.get('compiler', {}).get('method', method)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--method', choices=METHODS, default='fit')
    parser.add_argument('--jobs', type=int, default=min(6, os.cpu_count() or 2))
    args = parser.parse_args()
    keys = [item['id'] for item in LookLibrary().list()]
    failures = []
    complete = []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(rebuild_one, (key, args.method)): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                complete.append(future.result())
                print(f'[{len(complete):3d}/{len(keys)}] {key}', flush=True)
            except Exception as error:
                failures.append({'id': key, 'error': str(error)})
                print(f'[FAIL] {key}: {error}', flush=True)
    result = {'schema': 1, 'method': args.method, 'jobs': args.jobs,
              'complete': len(complete), 'failed': failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
