import os
import sys

import pytest

# Make `geox_bridge` importable when running pytest from anywhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(scope="session")
def qgis_app():
    """One QgsApplication for the whole session.

    A second instance (or exitQgis + re-init) crashes the interpreter, so
    this is the only place it is created; QGIS-dependent test modules take
    it as a fixture. exitQgis is deliberately skipped - the process is
    about to die anyway and teardown segfaults on some builds.
    """
    qgis_core = pytest.importorskip("qgis.core", reason="run under QGIS's bundled Python")
    app = qgis_core.QgsApplication([], False)
    app.initQgis()
    yield app
