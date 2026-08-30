"""JSON-driven patients / suppliers / workflows and request (de)serialisation."""
from supplier_caller.catalog import Catalog
from supplier_caller.models import CallRequest


def test_catalog_loads_bundled_json() -> None:
    c = Catalog.load()
    assert c.patient("pt-eleanor-martinez").hcpcs == "K0001"
    assert c.supplier("sup-lakeview").phone.startswith("+1")
    wf = c.workflow("supplier_check")
    assert wf.concurrency >= 1 and wf.caller_agent == "supplier-caller"
    assert "pt-robert-chen" in [p.id for p in c.patients]


def test_build_request_round_trips_through_dict() -> None:
    c = Catalog.load()
    req = c.build_request("pt-robert-chen", "sup-lakeview")
    assert req.patient.hcpcs == "E0601" and req.supplier.name == "Lakeview Medical Supply"
    again = CallRequest.from_dict(req.to_dict())
    assert again == req


def test_prompt_follows_the_patient_not_a_constant() -> None:
    from supplier_caller.prompts import build_caller_instructions

    c = Catalog.load()
    req = c.build_request("pt-robert-chen", "sup-lakeview")
    text = build_caller_instructions(req)
    assert "E0601" in text and "60601" in text and "Robert Chen" in text
    assert "K0001" not in text and "60640" not in text
