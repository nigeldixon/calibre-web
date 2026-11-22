import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"
CONSTANTS_PATH = SCRIPTS_DIR / "report_constants.json"
GENERATORS = {
    "codeql_pr_comment.py": "codeql",
    "semgrep_pr_comment.py": "semgrep",
    "test_coverage_comment.py": "tests",
}


def load_json_constants():
    return json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))


def test_python_loader_matches_json():
    spec = importlib.util.spec_from_file_location(
        "report_constants", SCRIPTS_DIR / "report_constants.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.load_constants() == load_json_constants()



@pytest.mark.parametrize("name,key", GENERATORS.items())
def test_generators_do_not_hardcode_shared_constants(name, key):
    text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    constants = load_json_constants()
    assert "load_constants()" in text
    assert constants["markers"][key] not in text
    assert constants["headings"][key] not in text
    if key != "tests":
        for label in constants["labels"].values():
            assert label not in text
