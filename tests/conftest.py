"""Standalone smoke tests for the published skill. Adds the skill dir to sys.path
so `import bannerlib...` works without the upstream repo."""
import os
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SKILL_DIR = os.path.join(REPO_ROOT, "skills", "validate-banner")
SAMPLES_DIR = os.path.join(REPO_ROOT, "samples")
sys.path.insert(0, SKILL_DIR)


@pytest.fixture
def zip_sample(tmp_path):
    def make(name):
        d = os.path.join(SAMPLES_DIR, name)
        out = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(os.listdir(d)):
                zf.write(os.path.join(d, f), f)
        return str(out)
    return make
