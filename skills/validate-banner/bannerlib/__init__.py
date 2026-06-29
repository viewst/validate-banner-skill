"""Pure-Python port of the banner-validator engine (validators + rules + input
adapters + autofix). Mirrors src/{rules,validators,lib} from the banner-validator
repo. Stdlib-only; Pillow is an OPTIONAL dependency used solely by the --fix
compress-to-fit step (bannerlib.fixes). The ruleset is read from the generated
rules.json (single source of truth: src/rules/networks.ts)."""
