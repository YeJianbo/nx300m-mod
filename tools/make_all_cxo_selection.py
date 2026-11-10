"""Generate a selection table containing every CXO FilmPack catalogue entry."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'mod/nxconnect/NXConnect/app/src/main/assets/cxo-filmpack/films.json'
OUT = ROOT / 'tools/cxo-filmpacks-all.tsv'


def main() -> None:
    films = json.loads(SOURCE.read_text(encoding='utf-8'))
    OUT.write_text(''.join(
        f"{film['id']}\t{film['category']}\tCXO {film['name']}\n" for film in films
    ), encoding='utf-8', newline='\n')
    print(f'{len(films)} entries -> {OUT}')


if __name__ == '__main__':
    main()
