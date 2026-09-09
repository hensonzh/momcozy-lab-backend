import json
from pathlib import Path

import pytest

from app.infrastructure.care_report_contract import ReportGenerationInput, ReportGenerationResult
from app.infrastructure.care_report_sources_contract import ReportSourceQuery, ReportSourcesRead


def canonical_schema(value):
    # FastAPI omits explicit null defaults; optionality is still checked through required/anyOf.
    if isinstance(value, dict):
        return {key: canonical_schema(child) for key, child in value.items() if not (key == 'default' and child is None)}
    if isinstance(value, list):
        return [canonical_schema(child) for child in value]
    return value


@pytest.mark.parametrize('model,mode', [(ReportGenerationInput, 'validation'), (ReportGenerationResult, 'serialization'),
    (ReportSourceQuery, 'validation'), (ReportSourcesRead, 'serialization')])
def test_product_report_client_matches_the_runtime_owned_contract(model, mode):
    document = json.loads((Path(__file__).parents[1] / 'docs/runtime-care-report-contract.json').read_text())
    schema = model.model_json_schema(ref_template='#/components/schemas/{model}', mode=mode)
    definitions = schema.pop('$defs', {})
    for name, value in {model.__name__: schema, **definitions}.items():
        assert canonical_schema(value) == canonical_schema(document['schemas'][name]), name
