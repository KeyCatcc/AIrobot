"""Update registered catalog entries from an operator batch; preview by default."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from action_catalog import DEFAULT_CATALOG, validate_catalog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('batch', type=Path)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    raw = DEFAULT_CATALOG.read_bytes()
    catalog = json.loads(raw.decode('utf-8-sig'))
    rows = {r['name']: r for r in catalog['actions']}
    if len(rows) != len(catalog['actions']):
        raise ValueError('Duplicate catalog entries')
    edits = json.loads(args.batch.read_text(encoding='utf-8'))['actions']
    seen = set()
    for edit in edits:
        name = edit['name']
        if name not in rows or name in seen or set(edit) - {'name', 'enabled', 'amplitude', 'description'}:
            raise ValueError('Unknown, duplicate or unsupported edit: ' + name)
        seen.add(name)
        print(name, json.dumps(edit, ensure_ascii=False))
        rows[name].update(edit)
    # Validate the prospective catalog without changing the live file.
    validate_catalog(catalog)
    if not args.apply:
        print('PREVIEW only; --apply saves all edits together')
        return
    if DEFAULT_CATALOG.read_bytes() != raw:
        raise RuntimeError('Catalog changed; retry')
    DEFAULT_CATALOG.with_name(DEFAULT_CATALOG.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak').write_bytes(raw)
    tmp = DEFAULT_CATALOG.with_suffix('.tmp')
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    tmp.replace(DEFAULT_CATALOG)
    print('Saved. Restart robot service then RAI once. No hardware commands sent.')


if __name__ == '__main__':
    main()
