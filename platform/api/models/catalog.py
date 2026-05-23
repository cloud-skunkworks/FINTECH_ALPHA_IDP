"""Catalog models."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class TemplateParameter(BaseModel):
    """A single parameter definition for a catalog template."""

    name: str
    type: Literal["string", "integer", "boolean", "enum"]
    description: str
    required: bool = True
    default: Any | None = None
    enum_values: list[str] | None = None
    pattern: str | None = None
    min_value: int | None = None
    max_value: int | None = None


class CatalogTemplate(BaseModel):
    """A registered infrastructure provisioning template."""

    template_id: str = Field(description="Unique template identifier.")
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    owner: str = Field(default="platform-engineering")
    type: Literal["service", "database", "storage", "queue", "function"] = "service"
    requires_irsa: bool = Field(
        default=True,
        description="Whether IRSA is provisioned for this template (required for EKS workloads).",
    )
    parameters: list[TemplateParameter] = Field(default_factory=list)
    provisions: list[str] = Field(
        description="AWS resources provisioned by this template.",
        default_factory=list,
    )
    estimated_cost_monthly_usd: float | None = Field(
        default=None,
        description="Approximate monthly cost in USD for 'sm' size. Actual cost depends on usage.",
    )
