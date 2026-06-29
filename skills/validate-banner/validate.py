#!/usr/bin/env python3
"""validate-banner skill — standalone Python CLI.

Validates an HTML5 banner (zip archive or single HTML file) against ad-network
requirements, using the same ruleset/validators as the banner-validator web app.
Stdlib-only for validation and the URL-strip --fix; Pillow is optional and only
needed for --fix image compression.

Run:  python3 validate.py <banner.zip | banner.html> [options]
See:  python3 validate.py --help
"""
import os
import sys

# Make the bundled `bannerlib` package importable no matter the cwd or whether
# this file was reached through a symlink (npx skills / ~/.claude/skills install).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bannerlib.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
