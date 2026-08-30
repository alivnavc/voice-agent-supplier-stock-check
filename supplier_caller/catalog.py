"""JSON-backed catalog: patients, suppliers, workflow configs, caller identity."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import CallRequest, Patient, Supplier

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class PatientRecord(Patient):
    id: str = ""


@dataclass(frozen=True)
class SupplierRecord(Supplier):
    id: str = ""
    city: str = ""


@dataclass(frozen=True)
class WorkflowConfig:
    id: str
    description: str
    caller_agent: str
    concurrency: int = 1
    max_attempts: int = 2
    visibility_timeout_s: int = 600
    call_timeout_s: int = 480
    extra: dict[str, Any] = field(default_factory=dict)


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


@dataclass
class Catalog:
    patients: list[PatientRecord]
    suppliers: list[SupplierRecord]
    workflows: dict[str, WorkflowConfig]
    caller: dict[str, str]

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> Catalog:
        patients = [PatientRecord(**p) for p in _load(data_dir / "patients.json")]
        suppliers = [SupplierRecord(**s) for s in _load(data_dir / "suppliers.json")]
        workflows: dict[str, WorkflowConfig] = {}
        for wid, cfg in _load(data_dir / "workflows.json").items():
            known = {k: v for k, v in cfg.items() if k in WorkflowConfig.__dataclass_fields__}
            extra = {k: v for k, v in cfg.items() if k not in WorkflowConfig.__dataclass_fields__}
            workflows[wid] = WorkflowConfig(id=wid, extra=extra, **known)
        caller = _load(data_dir / "caller.json") if (data_dir / "caller.json").exists() else {}
        return cls(patients, suppliers, workflows, caller)

    def patient(self, pid: str) -> PatientRecord:
        return next(p for p in self.patients if p.id == pid)

    def supplier(self, sid: str) -> SupplierRecord:
        return next(s for s in self.suppliers if s.id == sid)

    def workflow(self, wid: str) -> WorkflowConfig:
        return self.workflows[wid]

    def build_request(self, patient_id: str, supplier_id: str, phone: str | None = None) -> CallRequest:
        p, s = self.patient(patient_id), self.supplier(supplier_id)
        return CallRequest(
            patient=Patient(**{k: getattr(p, k) for k in Patient.__dataclass_fields__}),
            supplier=Supplier(name=s.name, phone=phone or s.phone),
            **self.caller,
        )

    def default_request(self) -> CallRequest:
        return self.build_request(self.patients[0].id, self.suppliers[0].id)
