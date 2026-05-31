from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class RawSqlRequest(BaseModel):
    sql: str


class AddColumnRequest(BaseModel):
    column_name: str
    data_type: str
    nullable: bool = True
    default: Optional[str] = None


class ToggleRLSRequest(BaseModel):
    enabled: bool


class RLSPolicyCreate(BaseModel):
    table_name: str
    policy_name: str
    permissive: str = "PERMISSIVE"
    command: str = "ALL"
    roles: List[str] = []
    using: Optional[str] = None
    with_check: Optional[str] = None


class RLSPolicyUpdate(BaseModel):
    permissive: Optional[str] = None
    command: Optional[str] = None
    roles: Optional[List[str]] = None
    using: Optional[str] = None
    with_check: Optional[str] = None


class FunctionDDLRequest(BaseModel):
    definition: str


class CreateIndexRequest(BaseModel):
    index_name: str
    columns: List[str]
    unique: bool = False
    where_clause: Optional[str] = None
