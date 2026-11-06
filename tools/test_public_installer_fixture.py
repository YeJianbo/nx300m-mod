"""Check the public payload, original additions, and import without stock data."""
from pathlib import Path
import json
import shutil
import struct
import tempfile
from unittest.mock import patch

import nx300m_installer as app

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'outputs/releases/windows-updater-20261106/public-package'
IDENTITY={'serial':'FIXTURE','label':'temporary','filesystem':'exFAT'}


def main():
    with tempfile.TemporaryDirectory(prefix='nx-public-install-') as temp:
        root=Path(temp);card=root/'card';card.mkdir()
        with patch.object(app,'BASE',root/'program'),patch.object(app,'FIRST',PACKAGE/'first-install'),patch.object(app,'volume',return_value=IDENTITY):
            app.install_first(card,IDENTITY)
            assert app.verify_backend(lambda name:(card/name).read_bytes())['session']==1
            _,rows=app.catalog(card);assert len(rows)==6
            assert not list(card.rglob('table-stock3d.d4lut'))
            for row in rows:
                folder=card/'nx300m-mod/looks'/row[0]
                metadata=json.loads((folder/'look.json').read_text(encoding='utf-8'))
                assert metadata['license']=='CC0-1.0' and metadata['source_kind']=='original-procedural'
                table=struct.unpack('<2190H',(folder/'table.d4lut').read_bytes())
                assert max(table)<=1023 and all(table[i]==0 for i in (729,1459,2189))
            older=root/'older';(older/'nx300m-mod/looks').mkdir(parents=True)
            older_rows=[row.copy() for row in rows[:2]];older_rows[0][1]='用户原名称'
            (older/'nx300m-mod/looks.txt').write_text(''.join('|'.join(row)+'\n' for row in older_rows),encoding='utf-8')
            for row in older_rows:
                shutil.copytree(card/'nx300m-mod/looks'/row[0],older/'nx300m-mod/looks'/row[0])
            photo=older/'photo.jpg';photo.write_bytes(b'keep')
            count,backup=app.add_builtin_looks(older,IDENTITY)
            assert count==4 and backup.is_dir() and photo.read_bytes()==b'keep'
            assert app.catalog(older)[1][0][1]=='用户原名称'
            assert app.add_builtin_looks(older,IDENTITY)==(0,None)
            cube=root/'identity.cube'
            cube.write_text('LUT_3D_SIZE 2\n'+''.join(f'{r} {g} {b}\n' for b in range(2) for g in range(2) for r in range(2)))
            key,_=app.import_cube(older,IDENTITY,cube,'导入测试')
            assert (older/'nx300m-mod/looks'/key/'table.d4lut').stat().st_size==4380
            assert not (older/'nx300m-mod/looks'/key/'table-stock3d.d4lut').exists()
    print('public fixture passed: ABI7 first install, six CC0 tables, four additions, preserved user data, import without factory data')


if __name__=='__main__':main()
