from __future__ import annotations

from pydantic import BaseModel


class RoleUpdate(BaseModel):
    role: str


class ApprovalUpdate(BaseModel):
    approval_status: str


class StatsResponse(BaseModel):
    users: int
    books: int
    requests: int
    loans: int


class ArchiveUpdate(BaseModel):
    archived: bool
