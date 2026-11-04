"""The everyday camera catalog is a selection from the complete source library."""
import json


def camera_looks(library):
    path = library.root/'catalog.json'
    if not path.exists():
        return library.list()
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if catalog.get('version') != 1:
        raise ValueError('Unsupported LUT catalog version')
    keys = [key for group in catalog['groups'] for key in group['ids']]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate LUT in camera catalog')
    return [library.get(key) for key in keys]
