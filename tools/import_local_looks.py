"""Import selected installed Ricoh/NX300 and camera creative LookTables locally."""
import json
import os
from pathlib import Path
from adobe_profiles import DcpProfile
from look_library import LookLibrary
from import_fuji_rgb_tables import import_profiles

PROJECT = Path(__file__).resolve().parents[1]

def main():
    library = LookLibrary()
    user = Path(os.environ['APPDATA'])/'Adobe/CameraRaw/CameraProfiles'
    sources = [
        (user/'Imported/NX300_DCP_Camera_Profiles_v1/NX300 GR IV Positive Film.dcp', 'ricoh-positive-exact', 'GR Pos'),
        (user/'Imported/NX300_DCP_Camera_Profiles_v1/NX300 GR IV Negative Film.dcp', 'ricoh-negative-exact', 'GR Neg'),
    ]
    # The installed filenames omit Exact; use the embedded names to distinguish
    # the user-selected pair from the separate v4 profiles.
    expected_names = {'ricoh-positive-exact': 'NX300 GR IV Positive Film Exact',
                      'ricoh-negative-exact': 'NX300 GR IV Negative Film Exact'}
    for source, key, _ in sources:
        if source.exists():
            profile = DcpProfile(source)
            if profile.get(50936) != expected_names[key] or profile.get(50708) != 'Samsung NX300':
                raise ValueError('Selected Ricoh profile identity does not match: '+str(source))
    for name, key, label in [('Astia Soft', 'astia', '富士 Astia'), ('Classic Chrome', 'classic-chrome', '富士 CC'), ('Eterna', 'eterna', '富士 Eterna'), ('Pro Neg Std', 'pro-neg-std', '富士 Pro Neg Std'), ('Reala Ace', 'reala-ace', '富士 Reala Ace'), ('Velvia Vivid', 'velvia', '富士 Velvia')]:
        sources.append((user/f'Imported/NX300_DCP_Camera_Profiles_v1/NX300 Fuji {name}.dcp', 'nx300-fuji-'+key, label))
    camera = Path('./local/lightroom/Resources/CameraProfiles/Camera')
    sources += [
        (camera/'Sony ILCE-7M4/Sony ILCE-7M4 Camera FL.dcp', 'sony-fl-look', 'Sony FL'),
        (camera/'Panasonic DC-S5M2/Panasonic DC-S5M2 Camera L Classic Neo.dcp', 'panasonic-classic-neo-look', 'Pana Classic Neo'),
        (camera/'Pentax K-3 Mark III/PENTAX K-3 Mark III Camera Bright.dcp', 'pentax-bright-look', 'Pentax Bright'),
    ]
    imported, missing = [], []
    for source, key, label in sources:
        if not source.exists(): missing.append(str(source)); continue
        if (library.directory(key)/'look.json').exists(): imported.append(library.get(key)); continue
        imported.append(library.add_dcp_look(source, key, label=label))
        print(key, flush=True)
    rgb_tables = import_profiles(library=library)
    imported.extend(library.get(entry['id']) for entry in rgb_tables['entries'])
    output = PROJECT/'outputs/looks/imported.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'imported': imported, 'missing': missing, 'scope': 'creative LookTable components; no donor sensor calibration transplanted'}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'imported': len(imported), 'missing': missing}, ensure_ascii=False))

if __name__ == '__main__': main()
