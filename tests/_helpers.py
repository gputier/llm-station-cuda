"""Shared test helpers for the suite adapter tests (RULER, LongBench v2):
building a config variant with a different --ctx-size/max_tokens, and a
SuiteContext against it. Factored here once instead of duplicated per module.
"""
from __future__ import annotations

import dataclasses
import pathlib

from benchrun.runner import SuiteContext


def cfg_with_ctx_size(cfg, ctx_size, max_tokens=None):
    args = list(cfg.args)
    args[args.index("--ctx-size") + 1] = str(ctx_size)
    sampling = dict(cfg.sampling)
    if max_tokens is not None:
        sampling["max_tokens"] = max_tokens
    return dataclasses.replace(cfg, args=args, sampling=sampling)


def suite_ctx(tmp_path: pathlib.Path, cfg) -> SuiteContext:
    return SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
