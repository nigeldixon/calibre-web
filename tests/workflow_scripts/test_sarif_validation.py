import json


def validation(tmp_path, load_quality_script):
    return load_quality_script("sarif_validation.py", tmp_path)


def valid_run():
    return {
        "tool": {"driver": {"name": "CodeQL"}},
        "results": [],
        "invocations": [{"executionSuccessful": True}],
    }


def test_load_sarif_reads_json(tmp_path, load_quality_script):
    api = validation(tmp_path, load_quality_script)
    path = tmp_path / "result.sarif"
    path.write_text(json.dumps({"version": "2.1.0", "runs": []}), encoding="utf-8")

    assert api["load_sarif"](path) == {"version": "2.1.0", "runs": []}


def test_load_sarif_rejects_unreadable_files(tmp_path, load_quality_script):
    api = validation(tmp_path, load_quality_script)
    malformed = tmp_path / "bad.sarif"
    malformed.write_text("{not json", encoding="utf-8")

    for path in (malformed, tmp_path / "missing.sarif"):
        try:
            api["load_sarif"](path)
        except ValueError as exc:
            assert "Invalid SARIF" in str(exc)
            assert str(path) in str(exc)
        else:
            raise AssertionError(f"{path} should not load")


def test_valid_message(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_message"]

    assert valid(None) is True
    assert valid({}) is True
    assert valid({"text": "hello"}) is True
    assert valid("bad") is False
    assert valid({"text": 1}) is False


def test_valid_artifact_location(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_artifact_location"]

    assert valid(None) is True
    assert valid({}) is True
    assert valid({"uri": "cps/a.py"}) is True
    assert valid({"uri": None}) is True
    assert valid("bad") is False
    assert valid({"uri": 1}) is False


def test_valid_location(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_location"]

    assert valid({}) is True
    assert valid({"physicalLocation": {}}) is True
    assert valid({"physicalLocation": {"artifactLocation": {"uri": "a.py"}}}) is True
    assert valid("bad") is False
    assert valid({"physicalLocation": "bad"}) is False
    assert valid({"physicalLocation": {"artifactLocation": {"uri": 1}}}) is False


def test_valid_result_shape(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_result_shape"]

    assert valid({}) is True
    assert valid({"locations": []}) is True
    assert valid({"level": "warning", "message": {"text": "finding"}}) is True
    assert valid("bad") is False
    assert valid({"level": 1}) is False
    assert valid({"message": "bad"}) is False
    assert valid({"locations": "bad"}) is False
    assert valid({"locations": ["bad"]}) is False
    assert valid({"relatedLocations": ["bad"]}) is False


def test_valid_notification_shape(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_notification_shape"]

    assert valid({}) is True
    assert valid({"descriptor": {}}) is True
    assert valid({"level": "warning", "descriptor": {"id": "rule"}}) is True
    assert valid({"locations": []}) is True
    assert valid("bad") is False
    assert valid({"level": 1}) is False
    assert valid({"message": "bad"}) is False
    assert valid({"descriptor": "bad"}) is False
    assert valid({"descriptor": {"id": 1}}) is False
    assert valid({"descriptor": {"id": "rule"}, "locations": "bad"}) is False
    assert valid({"locations": ["bad"]}) is False


def test_valid_run_shape(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_run_shape"]

    assert valid(valid_run()) is True
    assert valid({"tool": {"driver": {}}, "results": None}) is False
    assert valid({"tool": {"driver": {}}, "results": [], "invocations": None}) is True
    assert valid("bad") is False
    assert valid({"results": []}) is False
    assert valid({"tool": {}, "results": []}) is False
    assert valid({"tool": {"driver": {}}, "results": "bad"}) is False
    assert valid({"tool": {"driver": {}}, "results": ["bad"]}) is False
    assert valid({"tool": {"driver": {}}, "results": [], "invocations": "bad"}) is False
    assert valid({"tool": {"driver": {}}, "results": [], "invocations": [None]}) is False
    assert valid({
        "tool": {"driver": {}},
        "results": [],
        "invocations": [{"toolExecutionNotifications": "bad"}],
    }) is False
    assert valid({
        "tool": {"driver": {}},
        "results": [],
        "invocations": [{"toolExecutionNotifications": [None]}],
    }) is False
    assert valid({
        "tool": {"driver": {}},
        "results": [],
        "invocations": [{"toolExecutionNotifications": None}],
    }) is True


def test_valid_document(tmp_path, load_quality_script):
    valid = validation(tmp_path, load_quality_script)["valid_document"]

    assert valid({"runs": [valid_run()]}) is True
    assert valid("bad") is False
    assert valid({}) is False
    assert valid({"runs": []}) is False
    assert valid({"runs": "bad"}) is False
    assert valid({"runs": [{}]}) is False


def test_scan_failure(tmp_path, load_quality_script):
    failure = validation(tmp_path, load_quality_script)["scan_failure"]

    assert failure("bad") is None
    assert failure({}) is None
    assert failure({"runs": None}) is None
    assert failure({"runs": [{}]}) is None
    assert failure({"runs": [{"invocations": None}]}) is None
    assert failure({"runs": [{"invocations": [{"toolExecutionNotifications": None}]}]}) is None
    assert failure({"runs": [valid_run()]}) is None

    failed = {"runs": [{"invocations": [{"executionSuccessful": False}]}]}
    assert failure(failed) == "executionSuccessful=false"

    for level, message, expected in (
        ("error", {"text": "extraction failed"}, "extraction failed"),
        ("ERROR", {"text": "parse failed"}, "parse failed"),
        ("Error", {"text": None}, "error notification"),
        ("error", "bad", "error notification"),
        ("error", None, "error notification"),
    ):
        scan = {
            "runs": [{
                "invocations": [{
                    "toolExecutionNotifications": [
                        {"level": level, "message": message}
                    ]
                }]
            }]
        }
        assert failure(scan) == expected, (level, message)
