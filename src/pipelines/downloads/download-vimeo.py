"""Compatibility entrypoint; prefer chi-ciq-download-vimeo."""
# ruff: noqa: N999

from pipelines.downloads.vimeo import main

if __name__ == "__main__":
    raise SystemExit(main())
