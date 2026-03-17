"""CRM models: Contact, Lead, Pipeline, Deal, Activity, Task, Proposal."""
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.customer import Customer


class Contact(Base):
    """Contact (CRM)."""

    __tablename__ = "crm_contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer: Mapped["Customer | None"] = relationship("Customer", back_populates="contacts")


class Lead(Base):
    """Lead (CRM)."""

    __tablename__ = "crm_leads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telefone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="novo", nullable=False)
    score: Mapped[int | None] = mapped_column(nullable=True)
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_contacts.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Pipeline(Base):
    """Pipeline (funil)."""

    __tablename__ = "crm_pipelines"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    stages: Mapped[list["PipelineStage"]] = relationship(
        "PipelineStage",
        back_populates="pipeline",
        order_by="PipelineStage.ordem",
        cascade="all, delete-orphan",
    )


class PipelineStage(Base):
    """Pipeline stage (etapa do funil)."""

    __tablename__ = "crm_pipeline_stages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    pipeline_id: Mapped[int] = mapped_column(
        ForeignKey("crm_pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    ordem: Mapped[int] = mapped_column(default=0, nullable=False)
    probabilidade: Mapped[int | None] = mapped_column(nullable=True)

    pipeline: Mapped["Pipeline"] = relationship("Pipeline", back_populates="stages")


class Deal(Base):
    """Deal (negócio)."""

    __tablename__ = "crm_deals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    etapa_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_pipeline_stages.id"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    contato_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_contacts.id"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_leads.id"), nullable=True, index=True
    )
    data_fechamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="aberto", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Activity(Base):
    """Activity (atividade: ligação, email, reunião)."""

    __tablename__ = "crm_activities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tipo: Mapped[str] = mapped_column(String(64), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_leads.id"), nullable=True, index=True
    )
    deal_id: Mapped[int | None] = mapped_column(
        ForeignKey("crm_deals.id"), nullable=True, index=True
    )


class Task(Base):
    """Task (tarefa ligada a lead/deal)."""

    __tablename__ = "crm_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="pendente", nullable=False)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    relacionado_tipo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relacionado_id: Mapped[int | None] = mapped_column(nullable=True)


class Proposal(Base):
    """Proposal (proposta)."""

    __tablename__ = "crm_proposals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    deal_id: Mapped[int] = mapped_column(
        ForeignKey("crm_deals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arquivo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
