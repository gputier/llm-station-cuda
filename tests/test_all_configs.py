"""Completeness check for the 42 bench configurations (Task 15)."""
import pathlib

from benchrun.config import load_config

ROOT = pathlib.Path(__file__).parents[1] / "configs"
EXPECTED = {
    "99": ["muse", "qwen", "qwenu", "qwenf", "qwent", "tiel", "ornith", "kat", "nex", "spark", "bonsai", "bonsai2", "xing", "veriloop", "hemmingway",
           "qwen36apex", "katapex", "occamy"],
    "97": ["tiel", "qwen36", "bonsai2", "qwen36apex", "katapex", "occamy"],
}


def test_no_model_folder_escapes_the_expected_list():
    on_disk = {p.name: sorted(m.name for m in p.iterdir() if m.is_dir()) for p in ROOT.iterdir() if p.is_dir()}
    assert on_disk == {machine: sorted(models) for machine, models in EXPECTED.items()}


def test_every_model_has_three_valid_configs():
    for machine, models in EXPECTED.items():
        for m in models:
            for v in ("R1", "R2", "R3"):
                cfg = load_config(ROOT / machine / m / f"{v}.yaml")
                assert (cfg.machine, cfg.model, cfg.variant) == (machine, m, v)


def test_r1_cites_the_model_card():
    for p in ROOT.glob("*/*/R1.yaml"):
        cfg = load_config(p)
        assert any("huggingface.co" in s for s in cfg.sources.values()), p
