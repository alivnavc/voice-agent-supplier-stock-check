from supplier_caller.models import CallRequest, Patient, Supplier
from supplier_caller.prompts import build_caller_instructions, build_opener


def test_caller_instructions_contain_patient_facts_and_rules() -> None:
    req = CallRequest(
        patient=Patient(name="Eleanor Martinez", age=72, coverage="Original Medicare Part B",
                        item="standard manual wheelchair", hcpcs="K0001", city="Chicago", zip_code="60640"),
        supplier=Supplier(name="Lakeview Medical Supply", phone=None),
    )
    text = build_caller_instructions(req)
    for needle in ["Eleanor Martinez", "K0001", "60640", "record_answer", "callee_on_hold", "end_call", "Medicare"]:
        assert needle in text


def test_opener_is_just_hi_who_and_how_are_you():
    from supplier_caller.agent import DEFAULT_REQUEST as req

    text = build_opener(req)
    assert req.caller_name in text and req.caller_org in text
    assert "how are you" in text.lower()
    assert req.patient.zip_code not in text and req.patient.item not in text  # the ask comes after they reply
    assert "good morning" not in text.lower() and "good evening" not in text.lower()
    assert text in build_caller_instructions(req)  # the model knows it was already said
