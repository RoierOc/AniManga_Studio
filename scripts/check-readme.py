#!/usr/bin/env python3
"""Check the README's local links, anchors and current screenshots; no network."""
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FILES = ('README.md', *(str(p.relative_to(ROOT)) for p in sorted((ROOT / 'docs').glob('*.md'))))

def anchors(text):
    found = set()
    for title in re.findall(r'^#{1,6}\s+(.+)$', text, re.M):
        slug = re.sub(r'[^\w\s-]', '', title.lower()).replace(' ', '-')
        found.add(slug)
    return found

def main():
    checked = 0
    for internal in ('docs/dev', 'docs/internal',
                     'docs/plan_tauri_desktop.md', 'docs/img/current/README.md'):
        assert not (ROOT / internal).exists(), f'Internal development document remains: {internal}'
    for name in FILES:
        path = ROOT / name
        content = path.read_text(encoding='utf-8')
        refs = re.findall(r'\]\(([^\s)]+)\)', content)
        refs += re.findall(r'(?:src|href)="([^"]+)"', content)
        for ref in refs:
            url = urlsplit(ref)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            assert target.is_relative_to(ROOT), f'Outside repository: {name}: {ref}'
            assert target.exists(), f'Missing: {name}: {ref}'
            if url.fragment and target.suffix == '.md':
                assert unquote(url.fragment) in anchors(target.read_text(encoding='utf-8')), f'Missing anchor: {name}: {ref}'
            checked += 1
    ET.parse(ROOT / 'docs/img/banner.svg')
    screenshots = list((ROOT / 'docs/img/current').glob('*.webp'))
    assert len(screenshots) == 8, 'Expected eight reviewed screenshots'
    for path in screenshots:
        raw = path.read_bytes()
        assert raw[:4] == b'RIFF' and raw[8:12] == b'WEBP', f'Invalid WebP: {path.name}'
    print(f'OK: {len(FILES)} guides, {checked} local references, eight WebP screenshots, no internal logs and SVG banner')

if __name__ == '__main__':
    main()
