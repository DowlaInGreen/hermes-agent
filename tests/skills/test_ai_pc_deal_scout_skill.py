"""Tests for optional-skills/research/ai-pc-deal-scout/scripts/score_listings.py"""

import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2] / "optional-skills" / "research" / "ai-pc-deal-scout"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import score_listings as sl  # noqa: E402

RATES = dict(sl.DEFAULT_RATES)


def _listing(**overrides) -> dict:
    base = {
        "platform": "njuskalo",
        "title": "PC Ryzen 5 5600 RTX 3060 12GB 32GB 1TB NVMe",
        "url": "https://example.test/1",
        "price": 520, "currency": "EUR", "shipping": 0,
        "cpu": "Ryzen 5 5600", "ram_gb": 32, "gpu": "RTX 3060", "vram_gb": 12,
        "ssd_type": "nvme", "ssd_gb": 1000, "form_factor": "tower",
        "location": "Zagreb", "origin_region": "zagreb",
        "condition": "used", "condition_described": True,
        "warranty_months": None, "returns": None, "seller_rating": None,
    }
    base.update(overrides)
    return base


def _score(**overrides) -> dict:
    return sl.score_listing(_listing(**overrides), RATES)


class TestScoreBounds:
    def test_score_within_0_100_and_breakdown_sums(self):
        r = _score()
        assert 0 <= r["score"] <= 100
        assert r["score"] == sum(r["breakdown"].values())

    def test_component_maxima_respected(self):
        r = _score(gpu="RTX 3090", vram_gb=24, ram_gb=128, ssd_gb=4000, price=1,
                   condition="refurbished", warranty_months=24, returns=True, seller_rating=100)
        b = r["breakdown"]
        assert b["cijena_dostava"] <= 25 and b["gpu_vram"] <= 25
        assert b["ram_nadogradivost"] <= 15 and b["ssd"] <= 10
        assert b["cpu"] <= 10 and b["stanje_povjerenje"] <= 10 and b["lokacija"] <= 5

    def test_verdict_matches_thresholds(self):
        for score, verdict in ((80, "kupiti"), (79, "razmotriti"), (65, "razmotriti"),
                               (64, "samo ako nema boljeg"), (50, "samo ako nema boljeg"),
                               (49, "preskočiti"), (0, "preskočiti")):
            assert sl._verdict(score) == verdict


class TestValueOrdering:
    def test_cheaper_identical_listing_scores_at_least_as_high(self):
        assert _score(price=400)["score"] >= _score(price=700)["score"]

    def test_more_vram_scores_higher_gpu_points(self):
        assert _score(gpu="RTX 3090", vram_gb=24)["breakdown"]["gpu_vram"] > \
            _score(gpu="RTX 3060", vram_gb=12)["breakdown"]["gpu_vram"]

    def test_4060ti_16gb_valued_above_8gb(self):
        g16 = sl.identify_gpu("RTX 4060 Ti", 16)
        g8 = sl.identify_gpu("RTX 4060 Ti", 8)
        assert g16["fair_eur"] > g8["fair_eur"]

    def test_pre_ryzen_amd_cpu_recognised_as_low_tier(self):
        assert sl.cpu_tier("AMD FX 8350") is not None
        assert sl.cpu_tier("AMD FX 8350") < sl.cpu_tier("Ryzen 5 5600")

    def test_old_gpu_valued_below_rtx_3060(self):
        assert sl.identify_gpu("GTX 970", 4)["fair_eur"] < sl.identify_gpu("RTX 3060", 12)["fair_eur"]

    def test_vram_parsed_from_gpu_string(self):
        assert sl.identify_gpu("RTX 4060 Ti 16GB", None)["vram_gb"] == 16


class TestCostsAndCurrency:
    def test_shipping_included_in_total(self):
        r = _score(shipping=30, origin_region="eu")
        assert r["total_eur"] == pytest.approx(550)

    def test_non_eu_adds_import_vat(self):
        eu = _score(origin_region="eu", shipping=20)
        uk = _score(origin_region="uk", shipping=20)
        assert uk["import_eur"] > 0 and eu["import_eur"] == 0
        assert uk["total_eur"] > eu["total_eur"]

    def test_currency_converted(self):
        r = _score(currency="GBP", price=100, shipping=0, origin_region="eu")
        assert r["price_eur"] == pytest.approx(100 * RATES["GBP"])

    def test_unknown_currency_excluded(self):
        assert _score(currency="XYZ")["excluded"] is True

    def test_unknown_shipping_flagged_not_guessed(self):
        r = _score(shipping=None, origin_region="eu")
        assert "dostava" in r["unknown"]


class TestHardRules:
    def test_laptop_is_skipped(self):
        r = _score(title="Gaming laptop RTX 3060")
        assert r["score"] == 0 and r["recommendation"] == "preskočiti"

    def test_no_ssd_capped_below_consider(self):
        assert _score(ssd_type="none", ssd_gb=None)["score"] < 50

    def test_unclear_condition_capped(self):
        assert _score(condition=None, condition_described=False)["score"] < 50

    def test_shipped_without_returns_capped_but_local_pickup_exempt(self):
        assert _score(origin_region="eu", returns=False)["score"] < 50
        assert _score(origin_region="zagreb", returns=False)["score"] >= 50

    def test_no_gpu_only_candidate_when_extremely_cheap(self):
        cheap = _score(gpu="none", vram_gb=None, price=180)
        pricey = _score(gpu="none", vram_gb=None, price=400)
        assert 50 <= cheap["score"] <= 64
        assert pricey["score"] < 50

    def test_8gb_vram_capped_unless_exceptional_price(self):
        assert _score(gpu="RTX 4060", vram_gb=8, price=700)["score"] <= 59

    def test_insufficient_data_excluded(self):
        r = _score(cpu=None, ram_gb=None, gpu=None, vram_gb=None)
        assert r["excluded"] is True and "nedovoljno" in r["exclude_reason"]

    def test_missing_price_excluded(self):
        assert _score(price=None)["excluded"] is True


class TestStateAndReport:
    def test_dedup_and_price_change(self, tmp_path):
        state = tmp_path / "seen.json"
        first = [_score(price=600)]
        sl.apply_state(first, state, "2026-10-06")
        assert first[0]["is_new"] is True
        second = [_score(price=550)]
        sl.apply_state(second, state, "2026-10-07")
        assert second[0]["is_new"] is False
        assert second[0]["price_change_eur"] == pytest.approx(-50)
        assert json.loads(state.read_text())["https://example.test/1"]["first_seen"] == "2026-10-06"

    def test_report_sections(self):
        results = [
            _score(url="a", price=450),
            _score(url="b", price=520, gpu="RTX 3090", vram_gb=24),
            _score(url="c", title="Gaming RGB PC", gpu="RTX 4060", vram_gb=8, price=900),
        ]
        report = sl.build_report(results, "2026-10-07")
        assert len(report["top5"]) <= 5
        scores = [r["score"] for r in report["top5"]]
        assert scores == sorted(scores, reverse=True)
        assert report["best_overall"] is report["top5"][0]
        assert report["avoid_example"]["url"] == "c"
        text = sl.render_text(report)
        for header in ("TOP 5", "NAJBOLJA UKUPNA KUPNJA", "NAJBOLJA JEFTINA OPCIJA", "IZBJEGAVATI"):
            assert header in text

    def test_weak_market_note(self):
        report = sl.build_report([_score(gpu="none", vram_gb=None, price=400)], "2026-10-07")
        assert report["market_note"]

    def test_cli_on_example_template(self, capsys):
        example = SKILL_DIR / "templates" / "listings.example.json"
        assert sl.main(["score", "--input", str(example), "--no-state", "--format", "json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["counts"]["scored"] >= 1
        for r in report["top5"]:
            assert {"platform", "title", "url", "total_eur", "score", "recommendation"} <= r.keys()

    def test_default_state_path_is_profile_aware(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert sl._default_state_path() == tmp_path / "ai-pc-deal-scout" / "seen.json"
