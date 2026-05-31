from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class BookCreate(BaseModel):
    catalog_id: UUID
    condition: str


class CatalogCreate(BaseModel):
    title: str
    author: str
    isbn: Optional[str] = None
    cover_url: Optional[str] = None


class RequestCreate(BaseModel):
    book_id: UUID
    message: Optional[str] = None


class RequestStatusUpdate(BaseModel):
    status: str


class TransferUpdate(BaseModel):
    status: Optional[str] = None
    scheduled_at: Optional[str] = None


class ReturnCreate(BaseModel):
    book_id: UUID


class SavedBookCreate(BaseModel):
    book_id: UUID
    catalog_id: UUID


class BookReviewCreate(BaseModel):
    book_id: UUID
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class UserReviewCreate(BaseModel):
    reviewee_id: UUID
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class ProfileUpdate(BaseModel):
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


class LibraryCreate(BaseModel):
    name: str
    city: str
    location: str
    is_self_service: bool = False


class LibraryBookAdd(BaseModel):
    catalog_id: UUID
    condition: str
