import pathlib
import pytest
import yaml
from benchrun.config import load_config, render_launch_spec, ConfigError

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_load_valid_config():
    cfg = load_config(FIX / "config_ok.yaml")
    assert cfg.model == "tiel" and cfg.variant == "R1"
    assert cfg.sampling["temperature"] == 0.6


def test_every_sampling_key_needs_a_source(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    del data["sources"]["top_k"]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="top_k"):
        load_config(p)


def test_variant_must_be_r1_r2_r3(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["variant"] = "R4"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="variant"):
        load_config(p)


def test_sampling_flags_forbidden_in_args(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["args"] += ["--temp", "0.3"]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="--temp"):
        load_config(p)


def test_render_launch_spec_names_instance_after_model_and_variant():
    spec = render_launch_spec(load_config(FIX / "config_ok.yaml"))
    assert spec["name"] == "bench-tiel-R1"
    assert spec["args"][:2] == ["-m", "D:\\models\\tiel\\Tiel.gguf"]
    assert set(spec) == {"name", "exe", "workDir", "cudaBin", "args", "env"}
