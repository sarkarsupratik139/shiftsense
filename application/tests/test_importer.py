import pandas as pd

from shiftsense import importer, optimizer
from shiftsense.constraints import check


def test_template_round_trips(policy, data):
    t = importer.template(data["agents"])
    agents, errors = importer.parse_agents(t, policy)
    assert errors == []
    assert list(agents.agent_id) == list(data["agents"].agent_id)
    assert agents.eligible.tolist() == data["agents"].eligible.tolist()


def test_validation_reports_problems(policy, data):
    t = importer.template(data["agents"])
    t.loc[0, "primary"] = "Insurance-FR"
    t.loc[1, "agent_id"] = t.loc[2, "agent_id"]
    t.loc[3, "secondary"] = "Claims-EN;Mystery"
    agents, errors = importer.parse_agents(t, policy)
    assert agents is None
    assert any("unknown queue 'Insurance-FR'" in e for e in errors)
    assert any("duplicate agent_id" in e for e in errors)
    assert any("Mystery" in e for e in errors)
    assert importer.parse_agents(pd.DataFrame({"name": ["x"]}), policy)[1][0].startswith("Missing column")


def test_imported_agents_get_a_compliant_roster(policy, data, req):
    t = importer.template(data["agents"]).head(40)          # a smaller site
    agents, errors = importer.parse_agents(t, policy)
    assert not errors
    leave, lerr = importer.parse_leave(pd.DataFrame({"agent_id": ["A01"], "date": ["2026-10-08"]}), agents, policy)
    assert not lerr and leave.day.tolist() == [3]
    d = importer.build_data(agents, leave, data, policy)
    res = optimizer.plan(policy, d["agents"], d["leave"], req)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert check(policy, d["agents"], d["leave"], res["roster"]) == []
    assert not ((res["roster"].agent_id == "A01") & (res["roster"].day == 3)).any()
