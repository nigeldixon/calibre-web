"""Shared test setup for application suites.

The cps package imports optional Google Drive helpers through several modules
at import time. Those dependencies are not installed in the test environment
and no unit test exercises Drive functionality, so install a small stub before
any cps module is imported.
"""
import sys
import types


# SyncToken imports jsonschema for header validation. It is optional in the
# application environment, so provide a no-op validator when it is unavailable.
try:
    import jsonschema  # noqa: F401
except ImportError:
    jsonschema = types.ModuleType("jsonschema")
    jsonschema.validate = lambda *args, **kwargs: None
    exceptions = types.ModuleType("jsonschema.exceptions")
    exceptions.ValidationError = Exception
    jsonschema.exceptions = exceptions
    sys.modules["jsonschema"] = jsonschema
    sys.modules["jsonschema.exceptions"] = exceptions

if "cps.gdriveutils" not in sys.modules:
    gdriveutils = types.ModuleType("cps.gdriveutils")
    gdriveutils.getFileFromEbooksFolder = lambda *args, **kwargs: None
    gdriveutils.do_gdrive_download = lambda *args, **kwargs: None
    gdriveutils.updateGdriveCalibreFromLocal = lambda *args, **kwargs: None
    sys.modules["cps.gdriveutils"] = gdriveutils
