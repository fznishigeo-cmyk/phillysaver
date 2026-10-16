"""Explicit historical publication fixtures; production privacy is tested separately."""

import hashlib
import re
import shutil

import pytest


@pytest.fixture
def pre_extension_privacy(tmp_path_factory, monkeypatch):
    """Keep old feature byte pins on their original privacy template, without repinning.

    Task 244's four-mode test checks the new privacy section and every other
    published byte against this exact, fingerprinted pre-task template.
    Only tests explicitly requesting this fixture use the historical page.
    """
    from dealradar.publish import site

    templates = tmp_path_factory.mktemp("pre-extension-privacy") / "templates"
    shutil.copytree(site.TEMPLATES_DIR, templates)
    privacy = templates / "privacy.html"
    legacy = re.sub(r"<h2>Browser extension</h2>.*?(?=<h2>Service providers and sharing</h2>)",
                    "", privacy.read_text(), flags=re.S)
    assert hashlib.sha256(legacy.encode()).hexdigest() == "f412ebdb20bfa471c49b7edcf424e50226d6fc868b98fe4da49d24aa93958538"
    privacy.write_text(legacy)
    monkeypatch.setattr(site, "TEMPLATES_DIR", templates)
