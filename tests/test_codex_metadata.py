import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

from gtoc_bench import ROOT
from gtoc_bench.agents import launch_spec
from gtoc_bench.codex_metadata import check_compatibility, model_catalog, write_catalog
from gtoc_bench.config import load_config


def config():
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["model"].update(id="gpt-6.1-terra", reasoning="max")
    return cfg


def catalog():
    return {
        "models": [
            {
                "slug": "gpt-6.1-terra",
                "context_window": 500000,
                "max_context_window": 872000,
                "auto_compact_token_limit": None,
                "minimal_client_version": "0.153.0",
                "model_messages": {},
            }
        ]
    }


def test_provider_catalog_is_pinned_and_configures_declared_context_without_guessing(tmp_path):
    (tmp_path / "provider-model.json").write_text(json.dumps(catalog()["models"][0]))
    data, source = model_catalog(config(), tmp_path)
    assert source == "provider_catalog" and data == catalog()
    path = write_catalog(data, tmp_path)
    launch_spec(config(), str(tmp_path), model_catalog_path=path)
    declared = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert declared["model"] == "gpt-6.1-terra" and declared["model_reasoning_effort"] == "max"
    assert declared["model_catalog_json"] == path and declared["model_context_window"] == 500000
    assert "model_auto_compact_token_limit" not in declared


def test_minimal_api_catalog_does_not_replace_the_bundled_codex_catalog(tmp_path):
    (tmp_path / "provider-model.json").write_text('{"id":"gpt-6.1-terra"}')
    assert model_catalog(config(), tmp_path) == (None, "bundled_codex_catalog")


def test_operator_catalog_is_explicit_and_incompatible_versions_are_rejected(tmp_path):
    cfg = config()
    path = tmp_path / "models.json"
    path.write_text(json.dumps(catalog()))
    cfg["model"]["catalog_file"] = str(path)
    assert model_catalog(cfg, tmp_path)[1] == "operator_catalog"
    data = catalog()
    data["models"][0]["minimal_client_version"] = "9.0.0"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="requires Codex"):
        model_catalog(cfg, tmp_path)
    data["models"][0]["slug"] = "different-model"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="exact requested model"):
        model_catalog(cfg, tmp_path)


def fake_cli(tmp_path, known=True, fallback=False):
    executable = tmp_path / "fake-codex"
    calls = tmp_path / "calls.jsonl"
    script = f"""#!{sys.executable}
import json,sys
from pathlib import Path
for line in sys.stdin:
 request=json.loads(line)
 method=request['method']
 with Path({str(calls)!r}).open('a') as out:out.write(json.dumps(method)+'\\n')
 if 'id' not in request:continue
 if method=='initialize':result={{}}
 elif method=='model/list':result={{'data':[{{'model':'gpt-6.1-terra','supportedReasoningEfforts':[{{'reasoningEffort':'max'}}]}}] if {known!r} else [],'nextCursor':None}}
 elif method=='thread/start':
  if {fallback!r}:print('Unknown model gpt-6.1-terra is used. This will use fallback model metadata.',file=sys.stderr,flush=True)
  result={{'model':'gpt-6.1-terra','reasoningEffort':'max'}}
 else:raise RuntimeError('Model inference must never run during preflight: '+method)
 print(json.dumps({{'id':request['id'],'result':result}}),flush=True)
"""
    executable.write_text(script)
    executable.chmod(0o700)
    return str(executable), calls


@pytest.mark.parametrize(
    "known,fallback,compatible", [(True, False, True), (False, False, False), (True, True, False)]
)
def test_rpc_preflight_checks_actual_cli_resolution_without_model_turns(
    tmp_path, known, fallback, compatible
):
    cli, calls = fake_cli(tmp_path, known, fallback)
    home = tmp_path / "private-home"
    home.mkdir()
    report = check_compatibility(config(), home, executable=cli)
    assert (report["status"] == "compatible") is compatible
    assert report["model_turns"] == 0
    methods = [json.loads(line) for line in calls.read_text().splitlines()]
    assert "turn/start" not in methods
    assert ("thread/start" in methods) is known
    if not known:
        assert report["fallback_blocked"]
    if fallback:
        assert report["fallback_selected_model"]


@pytest.mark.skipif(
    not os.environ.get("GTOC_TEST_CODEX_BINARY"),
    reason="Set GTOC_TEST_CODEX_BINARY for an offline pinned-client check",
)
def test_real_pinned_codex_detects_terra_fallback_and_accepts_provider_metadata(tmp_path):
    binary = os.environ["GTOC_TEST_CODEX_BINARY"]
    source = os.environ.get("GTOC_TEST_CODEX_CATALOG")
    if not source:
        pytest.skip("Set GTOC_TEST_CODEX_CATALOG to an actual public provider-model.json")
    cfg = config()
    # The historical provider file is one ModelInfo; operator catalogs wrap models.
    raw = json.loads(Path(source).read_text())
    operator_catalog = tmp_path / "models.json"
    operator_catalog.write_text(json.dumps(raw if "models" in raw else {"models": [raw]}))
    cfg["model"]["catalog_file"] = str(operator_catalog)
    data, _ = model_catalog(cfg, tmp_path)
    plain = tmp_path / "without-catalog"
    plain.mkdir()
    result = check_compatibility(cfg, plain, executable=binary)
    assert result["status"] == "incompatible" and result["fallback_blocked"]
    home = tmp_path / "with-catalog"
    home.mkdir()
    path = write_catalog(data, tmp_path)
    result = check_compatibility(cfg, home, path, executable=binary)
    assert result["status"] == "compatible" and not result["fallback_selected_model"]
    assert result["context_window"] == 500000 and result["model_turns"] == 0
