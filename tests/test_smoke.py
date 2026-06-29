"""End-to-end smoke tests proving the published skill runs on stdlib alone."""
import json

from bannerlib.cli import run_cli
from bannerlib.inputs import bundle_from_zip_bytes
from bannerlib.rules import get_network
from bannerlib.validators import validate


def test_list_networks():
    code, out = run_cli(["--list-networks"])
    assert code == 0
    assert "amazon_2dsp" in out
    assert "google_ads" in out


def test_amazon_auto_detect(zip_sample):
    code, out = run_cli([zip_sample("amazon_external_font")])
    assert code == 1
    assert "amazon_2dsp" in out
    assert "fonts.googleapis.com" in out


def test_google_falls_back_to_matrix(zip_sample):
    code, out = run_cli([zip_sample("google_300x250")])
    assert code == 0
    assert "Fits (no errors):" in out


def test_json_payload(zip_sample):
    _, out = run_cli([zip_sample("amazon_external_font"), "--json"])
    payload = json.loads(out)
    assert payload["detected"] == "amazon_2dsp"
    assert payload["reports"][0]["verdict"] == "error"


def test_fix_strips_urls(zip_sample, tmp_path):
    out_path = str(tmp_path / "fixed.zip")
    _, out = run_cli(["--fix", "--network", "amazon_2dsp", zip_sample("amazon_external_font"), "-o", out_path])
    assert "Stripped" in out
    with open(out_path, "rb") as fh:
        data = fh.read()
    fixed = bundle_from_zip_bytes(data, len(data))
    ext = next(c for c in validate(fixed, get_network("amazon_2dsp")).checks if c.id == "external-urls")
    assert ext.severity != "error"
