"""The everyday camera catalog is a selection from the complete source library."""
import json


def camera_looks(library, *, complete=False):
    path = library.root/'catalog.json'
    if not path.exists():
        return library.list()
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if catalog.get('version') != 1:
        raise ValueError('Unsupported LUT catalog version')
    keys = [key for group in catalog['groups'] for key in group['ids']]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate LUT in camera catalog')
    selected=[library.get(key) for key in keys]
    if complete:
        # Preserve the familiar first entries. Only their short catalogue rows
        # are read on menu entry; native tables remain demand-loaded.
        selected += [look for look in library.list() if look['id'] not in set(keys)
                     and not look['id'].startswith(('native-', 'test-'))]
    return selected
