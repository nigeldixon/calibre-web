"""Shared test setup for application suites.

The cps package imports optional Google Drive helpers through several modules
at import time. Those dependencies are not installed in the test environment
and no unit test exercises Drive functionality, so install a small stub before
any cps module is imported.
"""
import sys
import types


if "cps.gdriveutils" not in sys.modules:
    gdriveutils = types.ModuleType("cps.gdriveutils")
    gdriveutils.getFileFromEbooksFolder = lambda *args, **kwargs: None
    gdriveutils.do_gdrive_download = lambda *args, **kwargs: None
    gdriveutils.updateGdriveCalibreFromLocal = lambda *args, **kwargs: None
    sys.modules["cps.gdriveutils"] = gdriveutils
