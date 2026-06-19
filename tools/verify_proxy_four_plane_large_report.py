#!/usr/bin/env python3
"""Compatibility wrapper for the strict #824/#826 full-report verifier."""

from __future__ import annotations

import sys

from verify_proxy_four_plane_final import main


if __name__ == "__main__":
    sys.exit(main())
