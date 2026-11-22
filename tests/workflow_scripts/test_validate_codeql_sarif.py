import json


RESULT_DIR = "codeql-results/python"


def write_sarif(tmp_path, data, name="result.sarif"):
    path = tmp_path / RESULT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


def valid_sarif():
    return {
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL"}},
                "results": [],
                "invocations": [{"executionSuccessful": True}],
            }
        ]
    }


def test_validate_codeql_sarif_requires_a_file(tmp_path, run_quality_script):
    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode != 0
    assert "No CodeQL SARIF was produced for" in proc.stderr


def test_validate_codeql_sarif_rejects_malformed_json(tmp_path, run_quality_script):
    write_sarif(tmp_path, "{not json")

    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode != 0
    assert "Invalid SARIF" in proc.stderr


def test_validate_codeql_sarif_rejects_invalid_documents(tmp_path, run_quality_script):
    invalid = {
        "no runs": {},
        "empty runs": {"runs": []},
        "missing tool": {"runs": [{"results": []}]},
        "empty tool": {"runs": [{"tool": {}, "results": []}]},
        "results not a list": {"runs": [{"tool": {"driver": {}}, "results": {}}]},
        "invocations not a list": {
            "runs": [{"tool": {"driver": {}}, "results": [], "invocations": "bad"}]
        },
        "invalid invocation": {
            "runs": [{"tool": {"driver": {}}, "results": [], "invocations": ["bad"]}]
        },
        "notifications not a list": {
            "runs": [
                {
                    "tool": {"driver": {}},
                    "results": [],
                    "invocations": [{"toolExecutionNotifications": "bad"}],
                }
            ]
        },
        "invalid notification": {
            "runs": [
                {
                    "tool": {"driver": {}},
                    "results": [],
                    "invocations": [{"toolExecutionNotifications": ["bad"]}],
                }
            ]
        },
    }
    for reason, document in invalid.items():
        write_sarif(tmp_path, document)
        proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])
        assert proc.returncode != 0, reason
        assert "Invalid SARIF" in proc.stderr, reason
        (tmp_path / RESULT_DIR / "result.sarif").unlink()


def test_validate_codeql_sarif_rejects_failed_scan(tmp_path, run_quality_script):
    document = valid_sarif()
    document["runs"][0]["invocations"][0]["executionSuccessful"] = False
    write_sarif(tmp_path, document)

    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode != 0
    assert "executionSuccessful=false" in proc.stderr


def test_validate_codeql_sarif_accepts_non_string_notification_descriptor_id(
    tmp_path, run_quality_script
):
    document = valid_sarif()
    document["runs"][0]["invocations"][0]["toolExecutionNotifications"] = [
        {"level": "warning", "descriptor": {"id": 7}, "message": {"text": "non-fatal"}}
    ]
    write_sarif(tmp_path, document)

    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode == 0, proc.stderr
    assert "Validated 1 CodeQL SARIF file(s) for python" in proc.stdout


def test_validate_codeql_sarif_rejects_error_notification(tmp_path, run_quality_script):
    document = valid_sarif()
    document["runs"][0]["invocations"][0]["toolExecutionNotifications"] = [
        {"level": "Error", "message": {"text": "extraction failed"}}
    ]
    write_sarif(tmp_path, document)

    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode != 0
    assert "extraction failed" in proc.stderr


def test_validate_codeql_sarif_accepts_valid_files(tmp_path, run_quality_script):
    with_warning = valid_sarif()
    with_warning["runs"][0]["invocations"][0]["toolExecutionNotifications"] = [
        {"level": "warning", "message": {"text": "non-fatal"}}
    ]
    write_sarif(tmp_path, with_warning, "one.sarif")
    write_sarif(tmp_path, valid_sarif(), "two.sarif")

    proc = run_quality_script("validate_codeql_sarif.py", tmp_path, args=["python"])

    assert proc.returncode == 0, proc.stderr
    assert "Validated 2 CodeQL SARIF file(s) for" in proc.stdout


def test_validate_codeql_sarif_accepts_explicit_results_dir(tmp_path, run_quality_script):
    path = tmp_path / "custom" / "python" / "result.sarif"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(valid_sarif()), encoding="utf-8")

    proc = run_quality_script(
        "validate_codeql_sarif.py", tmp_path, args=["python", "custom"]
    )

    assert proc.returncode == 0, proc.stderr
    assert "Validated 1 CodeQL SARIF file(s) for python" in proc.stdout


def test_validate_codeql_sarif_rejects_bad_arguments(tmp_path, run_quality_script):
    proc = run_quality_script(
        "validate_codeql_sarif.py", tmp_path, args=["python", "one", "two"]
    )

    assert proc.returncode != 0
    assert "Usage:" in proc.stderr
