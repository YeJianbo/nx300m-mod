"""Export a source-only, privacy-filtered snapshot without touching local assets.

This tool never creates a remote repository or pushes commits. Inspect its local
report and exported files before uploading. Source licensing is preserved per
component; firmware and externally derived look data are deliberately omitted.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('docs', 'runtime', 'tools', 'mod/metol', 'mod/deployment',
               'mod/quickshare', 'mod/nxconnect', 'looks/nx300-hard-mono')
SKIP_DIRS = {'.git', '.gradle', '.kotlin', '.cxx', '.idea', '.vscode',
             '__pycache__', 'build', 'target', 'node_modules', '_host',
             'upstream-rquickshare', 'images', 'captures'}
SKIP_NAMES = {'local.properties', 'keystore.properties', 'project.local.json',
              '.DS_Store', 'Thumbs.db', 'desktop.ini'}
SKIP_TOOLS = {'inspect_codex_sessions.py', 'parse_transcript.py',
              'filter_prompts.py', 'search_iso_keywords.py',
              'inspect_lr_favorites.py'}
TEXT_EXTS = {'.py', '.c', '.h', '.ld', '.sh', '.ps1', '.md', '.txt', '.json',
             '.xml', '.kt', '.java', '.kts', '.properties', '.toml', '.lock',
             '.yaml', '.yml', '.html', '.css', '.js', '.ts', '.proto', '.in',
             '.bat', '.pro', '.nx300m', '.cube', '.d4lut'}
BINARY_ASSETS = {
    'mod/nxconnect/NXConnect/gradle/wrapper/gradle-wrapper.jar',
    'mod/nxconnect/NXConnect/app/src/main/res/drawable-nodpi/home_device_photo.jpg',
    'mod/nxconnect/NXConnect/app/src/main/res/drawable-nodpi/home_device_cutout.png',
    'mod/nxconnect/NXConnect/app/src/main/res/drawable-nodpi/img_empty_gallery.png',
    'looks/nx300-hard-mono/table.d4lut',
    'looks/nx300-hard-mono/table-stock3d.d4lut',
}
SECRETS = re.compile(
    r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|'
    r'gh[pousr]_[A-Za-z0-9_]{25,}|github_pat_[A-Za-z0-9_]{25,}|'
    r'AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{25,}')


def allowed(p: Path) -> bool:
    rel = p.relative_to(ROOT)
    if SKIP_DIRS.intersection(rel.parts) or p.name in SKIP_NAMES:
        return False
    if p.is_symlink() or p.suffix.lower() in {'.jks', '.keystore', '.pem', '.key', '.log'}:
        return False
    if rel.as_posix() in BINARY_ASSETS:
        return True
    if rel.parts[0] == 'tools' and (p.suffix not in {'.py', '.c', '.sh', '.ps1'} or p.name in SKIP_TOOLS):
        return False
    if rel.as_posix() in {'docs/DELIVERY.md', 'docs/NX300_UPSTREAM_LUT_REVIEW.md',
                          'mod/nxconnect/tools/publish.ps1'}:
        return False
    if p.name in {'README.md', '.gitignore', '.gitattributes'}:
        # Use current integration descriptions instead of the old app landing page.
        if rel.as_posix() == 'mod/nxconnect/README.md':
            return False
    return (p.suffix.lower() in TEXT_EXTS or
            p.name in {'LICENSE', 'NOTICE', 'jsmn-LICENSE', 'gradlew',
                       '.gitignore', '.gitattributes', 'launch-profile',
                       'nx300m-camera-launch'})


def private_values() -> list[str]:
    # Read identifiers for negative matching only. They are never written to
    # the exported repository, manifest, terminal or report.
    values = [os.environ.get('USERNAME', ''), str(ROOT), str(Path.home())]
    signing = ROOT / 'mod/nxconnect/NXConnect/keystore.properties'
    if signing.exists():
        for line in signing.read_text('utf-8-sig').splitlines():
            if '=' in line and line.split('=', 1)[0].strip() in {'storePassword', 'keyPassword'}:
                values.append(line.split('=', 1)[1].strip())
    docs = '\n'.join(p.read_text('utf-8-sig') for p in (ROOT / 'docs').glob('*.md'))
    values += re.findall(r'card-([0-9A-Fa-f]{8})\b', docs)
    return sorted({v for v in values if len(v) >= 6}, key=len, reverse=True)


def sanitize(text: str, identifiers: list[str]) -> str:
    # Keep project-relative examples and remove the developer's directory tree.
    for root in {str(ROOT), ROOT.as_posix(), str(ROOT).replace('\\', '\\\\')}:
        text = text.replace(root, '.')
    text = re.sub(r'(?i)&\s*[\"\']?[A-Z]:[/\\]+conda[/\\]+envs[/\\]+nx300m-mod[/\\]+python\.exe[\"\']?',
                  'python', text)
    text = re.sub(r'(?i)[A-Z]:[/\\]+Adobe[/\\]+LR[/\\]+Adobe Lightroom Classic',
                  './local/lightroom', text)
    text = re.sub(r'(?i)[A-Z]:[/\\]+User[/\\]+[^\r\n`\"\']+',
                  './local/private-reference', text)
    text = re.sub(r'(?i)[A-Z]:[/\\]+codex[/\\]+project[/\\]+[0-9-]+[/\\]+[^\s`\"\']+',
                  './local/reference-project', text)
    text = re.sub(r'(?i)(?:[A-Z]:[/\\]+Users[/\\]+|./local/developer/\\]|\b))[^/\\\s\"\'`]+',
                  './local/developer', text)
    for value in identifiers:
        for variant in {value, value.replace('\\', '/'), value.replace('\\', '\\\\')}:
            text = re.sub(re.escape(variant), lambda _: 'LOCAL_REDACTED', text, flags=re.I)
    # Replace hardware-looking example MACs consistently across docs and tests.
    # Locally administered and broadcast addresses are already synthetic.
    mapping = {
        '02:30:0d:00:00:01': '02:30:0d:00:00:01',
        '02:30:0d:00:00:02': '02:30:0d:00:00:02',
        '02300d000001': '02300d000001', '02300d000002': '02300d000002',
    }
    for value, replacement in mapping.items():
        text = re.sub(re.escape(value), replacement, text, flags=re.I)
    # Uploaded phone screenshots and captures do not belong in the source snapshot.
    text = re.sub(r']*>', '', text, flags=re.I)
    text = re.sub(r'!\[[^\]]*\]\([^\n)]*\)', '', text)
    text = re.sub(r'https://github\.com/user-attachments/[^\s)\"<>]+', '[image omitted]', text)
    return text


def image_without_metadata(data: bytes, suffix: str) -> bytes:
    if suffix == '.jpg':
        # Remove EXIF/XMP/IPTC/comments while preserving JPEG scan data exactly.
        out = bytearray(data[:2]); at = 2
        if data[:2] != b'\xff\xd8':
            raise ValueError('Invalid JPEG asset')
        while at < len(data):
            if data[at] != 255:
                raise ValueError('Invalid JPEG marker')
            start = at
            while data[at] == 255:
                at += 1
            marker = data[at]; at += 1
            if marker == 0xda:
                out.extend(data[start:]); return bytes(out)
            size = int.from_bytes(data[at:at+2], 'big')
            if size < 2 or at + size > len(data):
                raise ValueError('Invalid JPEG segment')
            if marker not in {0xe1, 0xed, 0xfe}:
                out.extend(data[start:at+size])
            at += size
        raise ValueError('JPEG has no scan')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        pixels = im.copy()
        pixels.info.clear()
        output = io.BytesIO()
        pixels.save(output, format='PNG')
        return output.getvalue()


def audit(directory: Path, identifiers: list[str]) -> dict:
    issues = []; count = 0; total = 0; python_count = 0
    for p in sorted(directory.rglob('*')):
        if not p.is_file() or '.git' in p.relative_to(directory).parts:
            continue
        rel = p.relative_to(directory).as_posix(); data = p.read_bytes()
        count += 1; total += len(data)
        if p.name in SKIP_NAMES or p.suffix.lower() in {'.jks', '.keystore', '.pem', '.key', '.log'}:
            issues.append({'file': rel, 'kind': 'excluded-file'})
        if rel in BINARY_ASSETS:
            continue
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            issues.append({'file': rel, 'kind': 'unexpected-binary'}); continue
        if SECRETS.search(text):
            issues.append({'file': rel, 'kind': 'secret-pattern'})
        if any(v.lower() in text.lower() for v in identifiers):
            issues.append({'file': rel, 'kind': 'local-identifier'})
        private_session_pattern = r'\.' + 'codex-' + r'mobile-control|codex[/\\]+sessions'
        if re.search(private_session_pattern, text, re.I):
            issues.append({'file': rel, 'kind': 'private-session-reference'})
        if p.suffix == '.py':
            try:
                compile(data, rel, 'exec'); python_count += 1
            except (SyntaxError, ValueError) as exc:
                issues.append({'file': rel, 'kind': 'python-syntax',
                               'line': getattr(exc, 'lineno', None)})
    return {'files': count, 'bytes': total, 'python_syntax_checked': python_count,
            'issues': issues, 'passed': not issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    target = args.destination.resolve()
    identifiers = private_values()
    if not args.audit_only and (target == ROOT or ROOT in target.parents and not target.is_relative_to(ROOT / 'outputs/github-source')):
        parser.error('Use a separate export directory; local source is never rewritten')
    if not args.audit_only:
        if target.exists() and any(target.iterdir()):
            parser.error('Export destination must be empty; existing repositories are never replaced')
        files = [ROOT / 'environment.yml']
        for base in SOURCE_DIRS:
            for folder, dirs, names in os.walk(ROOT / base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                                 and not (Path(folder) / d).is_symlink())
                files.extend(p for name in names if (p := Path(folder) / name).is_file() and allowed(p))
        for p in sorted(files):
            rel = p.relative_to(ROOT); output = target / rel
            output.parent.mkdir(parents=True, exist_ok=True)
            data = p.read_bytes()
            if p.suffix.lower() in {'.png', '.jpg'}:
                output.write_bytes(image_without_metadata(data, p.suffix.lower()))
            elif rel.as_posix() in BINARY_ASSETS:
                output.write_bytes(data)
            else:
                output.write_text(sanitize(data.decode('utf-8-sig'), identifiers), encoding='utf-8', newline='\n')
        for source, destination in {
            'docs/repository/README.md': 'README.md',
            'docs/repository/THIRD_PARTY.md': 'THIRD_PARTY.md',
            'docs/repository/SOURCE_SCOPE.md': 'SOURCE_SCOPE.md',
            'docs/repository/gitignore': '.gitignore',
            'docs/repository/gitattributes': '.gitattributes',
            'docs/repository/project.example.json': 'project.example.json',
            'docs/repository/APP_README.md': 'mod/nxconnect/README.md',
            'docs/repository/DELIVERY.md': 'docs/DELIVERY.md',
            'docs/repository/INSTALL.md': 'docs/INSTALL.md',
            'docs/repository/MFW_SERVICES.md': 'docs/MFW_SERVICES.md',
            'docs/repository/NXCONNECT.md': 'docs/NXCONNECT.md',
            'docs/repository/QUICKSHARE.md': 'docs/QUICKSHARE.md',
            'docs/repository/LOOKS.md': 'docs/LOOKS.md',
            'docs/repository/MFW_EXPOSURE_TEST.md': 'docs/MFW_EXPOSURE_TEST.md',
        }.items():
            (target / destination).write_text(sanitize((ROOT/source).read_text('utf-8'), identifiers), encoding='utf-8', newline='\n')
        # Templates have been placed at their final paths; avoid duplicate copies.
        for p in (target / 'docs/repository').glob('*'):
            p.unlink()
        (target / 'docs/repository').rmdir()
    report = audit(target, identifiers)
    report_path = ROOT / 'outputs/github-source/privacy-audit.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
