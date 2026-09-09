"""Copy only the Runtime-owned care-report wire schemas from its reviewed OpenAPI export."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    document = json.loads(args.source.read_text())
    roots = ['ReportSourceQuery', 'ReportSourcesRead', 'ReportGenerationInput', 'ReportGenerationResult']
    schemas = {}
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in schemas:
            continue
        value = document['components']['schemas'][name]
        schemas[name] = value
        def refs(item):
            if isinstance(item, dict):
                if '$ref' in item:
                    pending.append(item['$ref'].rsplit('/', 1)[-1])
                for child in item.values():
                    refs(child)
            elif isinstance(item, list):
                for child in item:
                    refs(child)
        refs(value)
    content = json.dumps({'source': 'Agent Runtime OpenAPI', 'schemas': schemas}, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    destination = ROOT / 'docs/runtime-care-report-contract.json'
    if args.check:
        if destination.read_text() != content:
            raise SystemExit('Runtime report wire contract changed; sync both services.')
    else:
        destination.write_text(content)


if __name__ == '__main__':
    main()
