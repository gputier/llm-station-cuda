import json
import pathlib

import pytest
import yaml
from benchrun.config import ConfigError, load_config
from benchrun.profiles import (
    build_profile,
    profile_name,
    sampling_flags,
    write_profiles,
)
from test_all_configs import RETIRED

FIX = pathlib.Path(__file__).parent / "fixtures"
CONFIGS_ROOT = pathlib.Path(__file__).parents[1] / "configs"

EXPECTED_MODELS = {
    "99": ["muse", "qwen", "qwenu", "qwenf", "qwent", "tiel", "ornith", "kat", "nex",
           "spark", "bonsai", "bonsai2", "xing", "veriloop", "hemmingway",
           "qwen36apex", "katapex", "occamy"],
    "97": ["tiel", "qwen36", "bonsai2", "qwen36apex", "katapex", "occamy"],
}


def test_profile_name_uses_lowercase_r():
    cfg = load_config(FIX / "config_ok.yaml")
    assert profile_name(cfg) == "tiel-r1"


def test_sampling_flags_on_the_fixture_config():
    cfg = load_config(FIX / "config_ok.yaml")
    flags = sampling_flags(cfg)
    # temperature, top_p, top_k, min_p, max_tokens, in SAMPLING_TO_FLAG order.
    assert flags == [
        "--temp", "0.6", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.0",
        "-n", "16384",
    ]


def test_sampling_flags_never_duplicated():
    cfg = load_config(FIX / "config_ok.yaml")
    flags = sampling_flags(cfg)
    flag_names = flags[0::2]
    assert len(flag_names) == len(set(flag_names))


def test_chat_template_kwargs_become_one_json_flag(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["chat_template_kwargs"] = {"enable_thinking": False}
    data["sources"]["enable_thinking"] = "card"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    cfg = load_config(p)
    flags = sampling_flags(cfg)
    assert flags[-2] == "--chat-template-kwargs"
    assert json.loads(flags[-1]) == {"enable_thinking": False}


def test_unknown_sampling_key_is_refused(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["sampling"]["dry_multiplier"] = 0.1
    data["sources"]["dry_multiplier"] = "card"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    cfg = load_config(p)
    with pytest.raises(ConfigError, match="dry_multiplier"):
        sampling_flags(cfg)


def test_build_profile_keeps_the_base_args_and_appends_sampling():
    cfg = load_config(FIX / "config_ok.yaml")
    spec = build_profile(cfg)
    assert spec["name"] == "tiel-r1"
    assert spec["args"][:8] == cfg.args
    assert spec["args"][8:] == sampling_flags(cfg)
    assert set(spec) == {"name", "exe", "workDir", "cudaBin", "args", "env"}


@pytest.mark.parametrize("machine", ["99", "97"])
def test_write_profiles_for_a_whole_machine_has_no_duplicate_flag(tmp_path, machine):
    out_dir = tmp_path / machine
    written = write_profiles(machine, out_dir, CONFIGS_ROOT)
    expected_count = len(EXPECTED_MODELS[machine]) * 3 - sum(1 for r in RETIRED if r[0] == machine)
    assert len(written) == expected_count
    names = set()
    for path in written:
        spec = json.loads(path.read_text(encoding="utf-8"))
        names.add(spec["name"])
        args = spec["args"]
        sampling_only = [a for a in args if a.startswith("--") or a == "-n"]
        # Sampling/template flags are a suffix of args, each flag token appears
        # at most once among them (the underlying args never carry a sampling
        # flag at all, config.load_config already refuses that).
        seen = set()
        for token in sampling_only:
            if token in seen:
                raise AssertionError(f"{path}: flag {token} appears twice")
            seen.add(token)
    assert len(names) == expected_count


def test_every_bench_config_of_both_machines_yields_a_profile_without_error():
    total = 0
    for machine, models in EXPECTED_MODELS.items():
        for model in models:
            for variant in ("R1", "R2", "R3"):
                if (machine, model, variant) in RETIRED:
                    continue
                cfg = load_config(CONFIGS_ROOT / machine / model / f"{variant}.yaml")
                spec = build_profile(cfg)
                assert spec["name"] == f"{model}-r{variant[1]}"
                total += 1
    assert total == 72 - len(RETIRED)
