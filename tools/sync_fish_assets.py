"""Generate installer runtime copies and its Dart file list; --check is read-only."""
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).resolve().parents[1]


def sync(check=False):
    sources = sorted((ROOT / 'fish').glob('*.py'))
    target = ROOT / 'app/assets/fish'
    wizard = ROOT / 'app/lib/pages/wizard.dart'
    text = wizard.read_text(encoding='utf-8')
    entries = '\n'.join(f"        '{p.name}': 'assets/fish/{p.name}'," for p in sources)
    updated, count = re.subn(r'      const files = \{.*?\n      \};',
                             '      const files = {\n' + entries + '\n      };', text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError('Installer runtime map not found')
    mismatches = [p.name for p in sources if not (target/p.name).exists() or p.read_bytes() != (target/p.name).read_bytes()]
    extras = [p for p in target.glob('*.py') if p.name not in {s.name for s in sources}]
    deploy = sorted(p for p in (ROOT/'deploy').iterdir() if p.is_file() and p.suffix in ('.sh','.json'))
    deploy_target = ROOT/'app/assets/deploy'
    deploy_mismatches = [p.name for p in deploy if not (deploy_target/p.name).exists()
                         or p.read_bytes() != (deploy_target/p.name).read_bytes()]
    if check:
        if mismatches or extras or updated != text or deploy_mismatches:
            raise RuntimeError(f'Runtime assets out of sync: {mismatches}; extra files: {[p.name for p in extras]}; map: {updated != text}; deploy: {deploy_mismatches}')
    else:
        target.mkdir(parents=True, exist_ok=True)
        for p in sources:
            (target/p.name).write_bytes(p.read_bytes())
        if extras:
            raise RuntimeError('Remove obsolete assets explicitly: ' + str(extras))
        wizard.write_text(updated, encoding='utf-8', newline='\n')
        deploy_target.mkdir(parents=True, exist_ok=True)
        for p in deploy:
            (deploy_target/p.name).write_bytes(p.read_bytes())
    print(f'{len(sources)} runtime modules verified' if check else f'{len(sources)} runtime modules synchronized')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
