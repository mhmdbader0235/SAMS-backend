"""
Invitation Schema Layer.

Defines Pydantic request and response models for user invitations.
"""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class InvitationCreateRequest(BaseModel):
    email: EmailStr = Field(..., description="Target email address for invitation")
    tenant_id: str = Field(..., description="Target tenant ID (e.g., tenant_a)")
    role: str = Field(..., description="Target high-level role (e.g., teachers, managers, parents, student)")


class InvitationResponse(BaseModel):
    id: str = Field(..., description="Unique invitation ID (UUID)")
    email: str = Field(..., description="Target email address")
    tenant_id: str = Field(..., description="Target tenant ID")
    role: str = Field(..., description="Assigned role")
    status: str = Field(..., description="Invitation status (e.g., pending)")
    created_at: datetime = Field(..., description="Creation timestamp")
    message: str = Field(..., description="Confirmation message")
