import math

import numpy as np
import pytest

from shiftsense import data_gen, staffing
from shiftsense.config import load_policy, n_intervals, queue_names


def erlang_c_closed_form(n, a):
    """Textbook formula with factorials, used as an independent reference."""
    top = a ** n / math.factorial(n) * n / (n - a)
    bottom = sum(a ** k / math.factorial(k) for k in range(n)) + top
    return top / bottom


@pytest.mark.parametrize("n,a", [(11, 10.0), (5, 3.2), (20, 15.5), (3, 1.0)])
def test_erlang_c_matches_closed_form(n, a):
    assert staffing.erlang_c(n, a) == pytest.approx(erlang_c_closed_form(n, a), rel=1e-9)


def test_erlang_c_known_value():
    # classic textbook value: 10 Erlangs, 11 agents -> P(wait) ~ 0.6821
    assert staffing.erlang_c(11, 10.0) == pytest.approx(0.6821, abs=1e-4)


def test_erlang_c_unstable_queue_always_waits():
    assert staffing.erlang_c(10, 10.0) == 1.0
    assert staffing.service_level(9, 10.0, 180, 20) == 0.0


def test_agents_required_meets_sla_and_is_minimal():
    calls, aht, sla, target = 100, 180, 0.8, 20
    n = staffing.agents_required(calls, aht, 1800, sla, target)
    a = calls * aht / 1800
    assert staffing.service_level(n, a, aht, target) >= sla
    assert staffing.service_level(n - 1, a, aht, target) < sla


def test_agents_required_monotone_in_volume():
    prev = 0
    for calls in range(0, 200, 5):
        n = staffing.agents_required(calls, 300, 1800, 0.8, 20)
        assert n >= prev
        prev = n


def test_zero_volume_needs_nobody():
    assert staffing.agents_required(0, 300, 1800, 0.8, 20) == 0


def test_occupancy_cap_respected():
    n = staffing.agents_required(200, 300, 1800, 0.8, 60, max_occupancy=0.85)
    assert (200 * 300 / 1800) / n <= 0.85


def test_shrinkage_worked_example():
    # spec example: 18 productive / (1 - 0.30) ~= 26 scheduled
    assert staffing.scheduled_heads(18, 0.30) == 26


def test_staffing_chain_band_is_ordered():
    p = load_policy()
    d = data_gen.generate(p)
    st = staffing.staffing_chain(p, staffing.forecast(p, d["history"]))
    assert st["req"].shape == (len(queue_names(p)), n_intervals(p))
    assert np.all(st["req_low"] <= st["req_mid"])
    assert np.all(st["req_mid"] <= st["req_high"])
    assert np.all(st["req"] >= st["prod_mid"])


def test_closed_queue_hours_need_nobody():
    p = load_policy()
    d = data_gen.generate(p)
    st = staffing.staffing_chain(p, staffing.forecast(p, d["history"]))
    qi = queue_names(p).index("Claims-HI")
    assert st["req"][qi, 3 * 2] == 0          # 03:00 on day 0, queue closed


def test_generation_is_deterministic():
    p = load_policy()
    a, b = data_gen.generate(p), data_gen.generate(p)
    assert a["agents"].equals(b["agents"])
    assert a["history"].equals(b["history"])
    assert a["leave"].equals(b["leave"])


def test_skill_matrix_cross_skill_share():
    p = load_policy()
    d = data_gen.generate(p)
    agents = d["agents"]
    share = (agents.secondary.str.len() > 0).mean()
    assert 0.25 <= share <= 0.35
    for _, a in agents.iterrows():
        assert d["skills"].loc[a.agent_id, a.primary] == 2
        assert set(a.eligible) == {q for q in queue_names(p) if d["skills"].loc[a.agent_id, q] > 0}
