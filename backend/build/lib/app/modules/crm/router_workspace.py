"""Workspace CRM routes: contacts, leads, deals, pipeline, activities, tasks."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.user import User
from app.modules.crm.models import (
    Activity,
    Contact,
    Deal,
    Lead,
    Pipeline,
    PipelineStage,
    Task,
)
from app.modules.crm.service import on_deal_won
from app.modules.crm.schemas import (
    ActivityCreate,
    ActivityResponse,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    DealCreate,
    DealMoveRequest,
    DealResponse,
    DealUpdate,
    LeadCreate,
    LeadResponse,
    LeadUpdate,
    PipelineCreate,
    PipelineResponse,
    PipelineStageCreate,
    PipelineStageResponse,
    PipelineStageUpdate,
    PipelineUpdate,
    PipelineWithStagesResponse,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)

router = APIRouter(prefix="/crm", tags=["workspace-crm"])


def _org_id(current: User) -> str:
    return current.org_id or "innexar"


# --- Contacts (existing, keep crm:read / crm:write) ---


@router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("crm:read"))],
):
    """List contacts (workspace)."""
    r = await db.execute(select(Contact).order_by(Contact.id))
    return list(r.scalars().all())


@router.post("/contacts", response_model=ContactResponse, status_code=201)
async def create_contact(
    body: ContactCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("crm:write"))],
):
    """Create contact."""
    c = Contact(
        name=body.name,
        email=body.email,
        phone=body.phone,
        customer_id=body.customer_id,
    )
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


@router.get("/contacts/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("crm:read"))],
):
    """Get contact by id."""
    r = await db.execute(select(Contact).where(Contact.id == contact_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    return c


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: int,
    body: ContactUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("crm:write"))],
):
    """Update contact."""
    r = await db.execute(select(Contact).where(Contact.id == contact_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    if body.name is not None:
        c.name = body.name
    if body.email is not None:
        c.email = body.email
    if body.phone is not None:
        c.phone = body.phone
    if body.customer_id is not None:
        c.customer_id = body.customer_id
    await db.flush()
    await db.refresh(c)
    return c


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("crm:write"))],
):
    """Delete contact."""
    r = await db.execute(select(Contact).where(Contact.id == contact_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Contact not found")
    await db.delete(c)
    await db.flush()


# --- Leads ---


@router.get("/leads", response_model=list[LeadResponse])
async def list_leads(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.view"))],
    status: str | None = Query(None, description="Filter by status"),
    origem: str | None = Query(None, description="Filter by origem"),
    responsavel_id: int | None = Query(None, description="Filter by responsavel_id"),
):
    """List leads (workspace)."""
    org = _org_id(current)
    q = select(Lead).where(Lead.org_id == org).order_by(Lead.id.desc())
    if status is not None:
        q = q.where(Lead.status == status)
    if origem is not None:
        q = q.where(Lead.origem == origem)
    if responsavel_id is not None:
        q = q.where(Lead.responsavel_id == responsavel_id)
    r = await db.execute(q)
    return list(r.scalars().all())


@router.post("/leads", response_model=LeadResponse, status_code=201)
async def create_lead(
    body: LeadCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.create"))],
):
    """Create lead."""
    lead = Lead(
        org_id=_org_id(current),
        nome=body.nome,
        email=body.email,
        telefone=body.telefone,
        origem=body.origem,
        status=body.status,
        score=body.score,
        responsavel_id=body.responsavel_id or current.id,
    )
    db.add(lead)
    await db.flush()
    await db.refresh(lead)
    return lead


@router.get("/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.view"))],
):
    """Get lead by id."""
    r = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.org_id == _org_id(current))
    )
    lead = r.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: int,
    body: LeadUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Update lead."""
    r = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.org_id == _org_id(current))
    )
    lead = r.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if body.nome is not None:
        lead.nome = body.nome
    if body.email is not None:
        lead.email = body.email
    if body.telefone is not None:
        lead.telefone = body.telefone
    if body.origem is not None:
        lead.origem = body.origem
    if body.status is not None:
        lead.status = body.status
    if body.score is not None:
        lead.score = body.score
    if body.responsavel_id is not None:
        lead.responsavel_id = body.responsavel_id
    await db.flush()
    await db.refresh(lead)
    return lead


@router.delete("/leads/{lead_id}", status_code=204)
async def delete_lead(
    lead_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Delete lead."""
    r = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.org_id == _org_id(current))
    )
    lead = r.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await db.delete(lead)
    await db.flush()


# --- Pipeline ---


@router.get("/pipeline", response_model=list[PipelineWithStagesResponse])
async def list_pipelines(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """List pipelines with stages."""
    org = _org_id(current)
    r = await db.execute(
        select(Pipeline)
        .where(Pipeline.org_id == org)
        .options(selectinload(Pipeline.stages))
        .order_by(Pipeline.id)
    )
    pipelines = list(r.scalars().unique().all())
    out = []
    for p in pipelines:
        stages = getattr(p, "stages", None)
        if stages is None:
            stages = []
        out.append(
            PipelineWithStagesResponse(
                id=p.id,
                org_id=p.org_id,
                nome=p.nome,
                created_at=p.created_at,
                stages=[PipelineStageResponse.model_validate(s) for s in sorted(stages, key=lambda x: x.ordem)],
            )
        )
    return out


@router.post("/pipeline", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Create pipeline."""
    pipeline = Pipeline(org_id=_org_id(current), nome=body.nome)
    db.add(pipeline)
    await db.flush()
    await db.refresh(pipeline)
    return pipeline


@router.get("/pipeline/{pipeline_id}", response_model=PipelineWithStagesResponse)
async def get_pipeline(
    pipeline_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Get pipeline with stages."""
    r = await db.execute(
        select(Pipeline)
        .where(Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current))
        .options(selectinload(Pipeline.stages))
    )
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    stages = sorted(getattr(p, "stages", []) or [], key=lambda x: x.ordem)
    return PipelineWithStagesResponse(
        id=p.id,
        org_id=p.org_id,
        nome=p.nome,
        created_at=p.created_at,
        stages=[PipelineStageResponse.model_validate(s) for s in stages],
    )


@router.patch("/pipeline/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: int,
    body: PipelineUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Update pipeline."""
    r = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if body.nome is not None:
        p.nome = body.nome
    await db.flush()
    await db.refresh(p)
    return p


@router.delete("/pipeline/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Delete pipeline."""
    r = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    await db.delete(p)
    await db.flush()


# --- Pipeline stages ---


@router.get("/pipeline/{pipeline_id}/stages", response_model=list[PipelineStageResponse])
async def list_stages(
    pipeline_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """List stages of a pipeline."""
    r = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    r2 = await db.execute(
        select(PipelineStage)
        .where(PipelineStage.pipeline_id == pipeline_id)
        .order_by(PipelineStage.ordem, PipelineStage.id)
    )
    return list(r2.scalars().all())


@router.post("/pipeline/{pipeline_id}/stages", response_model=PipelineStageResponse, status_code=201)
async def create_stage(
    pipeline_id: int,
    body: PipelineStageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Create pipeline stage."""
    r = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    stage = PipelineStage(
        pipeline_id=pipeline_id,
        nome=body.nome,
        ordem=body.ordem,
        probabilidade=body.probabilidade,
    )
    db.add(stage)
    await db.flush()
    await db.refresh(stage)
    return stage


@router.patch("/pipeline/{pipeline_id}/stages/{stage_id}", response_model=PipelineStageResponse)
async def update_stage(
    pipeline_id: int,
    stage_id: int,
    body: PipelineStageUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Update pipeline stage."""
    r = await db.execute(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.pipeline_id == pipeline_id,
        )
    )
    stage = r.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")
    r2 = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    if r2.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if body.nome is not None:
        stage.nome = body.nome
    if body.ordem is not None:
        stage.ordem = body.ordem
    if body.probabilidade is not None:
        stage.probabilidade = body.probabilidade
    await db.flush()
    await db.refresh(stage)
    return stage


@router.delete("/pipeline/{pipeline_id}/stages/{stage_id}", status_code=204)
async def delete_stage(
    pipeline_id: int,
    stage_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.pipeline.manage"))],
):
    """Delete pipeline stage."""
    r = await db.execute(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.pipeline_id == pipeline_id,
        )
    )
    stage = r.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")
    r2 = await db.execute(
        select(Pipeline).where(
            Pipeline.id == pipeline_id, Pipeline.org_id == _org_id(current)
        )
    )
    if r2.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    await db.delete(stage)
    await db.flush()


# --- Deals ---


@router.get("/deals", response_model=list[DealResponse])
async def list_deals(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.view"))],
    etapa_id: int | None = Query(None),
    responsavel_id: int | None = Query(None),
    status: str | None = Query(None),
):
    """List deals (workspace)."""
    org = _org_id(current)
    q = select(Deal).where(Deal.org_id == org).order_by(Deal.id.desc())
    if etapa_id is not None:
        q = q.where(Deal.etapa_id == etapa_id)
    if responsavel_id is not None:
        q = q.where(Deal.responsavel_id == responsavel_id)
    if status is not None:
        q = q.where(Deal.status == status)
    r = await db.execute(q)
    return list(r.scalars().all())


@router.post("/deals", response_model=DealResponse, status_code=201)
async def create_deal(
    body: DealCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.edit"))],
):
    """Create deal."""
    deal = Deal(
        org_id=_org_id(current),
        titulo=body.titulo,
        valor=body.valor,
        etapa_id=body.etapa_id,
        responsavel_id=body.responsavel_id or current.id,
        contato_id=body.contato_id,
        lead_id=body.lead_id,
        data_fechamento=body.data_fechamento,
        status="aberto",
    )
    db.add(deal)
    await db.flush()
    await db.refresh(deal)
    return deal


@router.get("/deals/{deal_id}", response_model=DealResponse)
async def get_deal(
    deal_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.view"))],
):
    """Get deal by id."""
    r = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.org_id == _org_id(current))
    )
    deal = r.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.patch("/deals/{deal_id}", response_model=DealResponse)
async def update_deal(
    deal_id: int,
    body: DealUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.edit"))],
):
    """Update deal."""
    r = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.org_id == _org_id(current))
    )
    deal = r.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    if body.titulo is not None:
        deal.titulo = body.titulo
    if body.valor is not None:
        deal.valor = body.valor
    if body.etapa_id is not None:
        deal.etapa_id = body.etapa_id
    if body.responsavel_id is not None:
        deal.responsavel_id = body.responsavel_id
    if body.contato_id is not None:
        deal.contato_id = body.contato_id
    if body.lead_id is not None:
        deal.lead_id = body.lead_id
    if body.data_fechamento is not None:
        deal.data_fechamento = body.data_fechamento
    if body.status is not None:
        deal.status = body.status
    await db.flush()
    if deal.status == "ganho":
        await on_deal_won(db, deal)
    await db.refresh(deal)
    return deal


@router.patch("/deals/{deal_id}/move", response_model=DealResponse)
async def move_deal(
    deal_id: int,
    body: DealMoveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.move"))],
):
    """Move deal to another stage."""
    r = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.org_id == _org_id(current))
    )
    deal = r.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    deal.etapa_id = body.etapa_id
    await db.flush()
    await db.refresh(deal)
    return deal


@router.delete("/deals/{deal_id}", status_code=204)
async def delete_deal(
    deal_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.deals.edit"))],
):
    """Delete deal."""
    r = await db.execute(
        select(Deal).where(Deal.id == deal_id, Deal.org_id == _org_id(current))
    )
    deal = r.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    await db.delete(deal)
    await db.flush()


# --- Activities ---


@router.get("/activities", response_model=list[ActivityResponse])
async def list_activities(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.view"))],
    lead_id: int | None = Query(None),
    deal_id: int | None = Query(None),
):
    """List activities (filter by lead_id or deal_id)."""
    q = select(Activity).order_by(Activity.data.desc())
    if lead_id is not None:
        q = q.where(Activity.lead_id == lead_id)
    if deal_id is not None:
        q = q.where(Activity.deal_id == deal_id)
    r = await db.execute(q)
    return list(r.scalars().all())


@router.post("/activities", response_model=ActivityResponse, status_code=201)
async def create_activity(
    body: ActivityCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Create activity."""
    act = Activity(
        tipo=body.tipo,
        descricao=body.descricao,
        usuario_id=current.id,
        lead_id=body.lead_id,
        deal_id=body.deal_id,
    )
    db.add(act)
    await db.flush()
    await db.refresh(act)
    return act


# --- Tasks ---


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.view"))],
    relacionado_tipo: str | None = Query(None),
    relacionado_id: int | None = Query(None),
):
    """List tasks (optionally filter by relacionado_tipo/relacionado_id)."""
    q = select(Task).order_by(Task.data_vencimento.asc().nulls_last(), Task.id.desc())
    if relacionado_tipo is not None:
        q = q.where(Task.relacionado_tipo == relacionado_tipo)
    if relacionado_id is not None:
        q = q.where(Task.relacionado_id == relacionado_id)
    r = await db.execute(q)
    return list(r.scalars().all())


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    body: TaskCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Create task."""
    task = Task(
        titulo=body.titulo,
        descricao=body.descricao,
        data_vencimento=body.data_vencimento,
        status=body.status,
        usuario_id=current.id,
        relacionado_tipo=body.relacionado_tipo,
        relacionado_id=body.relacionado_id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.view"))],
):
    """Get task by id."""
    r = await db.execute(select(Task).where(Task.id == task_id))
    task = r.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Update task."""
    r = await db.execute(select(Task).where(Task.id == task_id))
    task = r.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if body.titulo is not None:
        task.titulo = body.titulo
    if body.descricao is not None:
        task.descricao = body.descricao
    if body.data_vencimento is not None:
        task.data_vencimento = body.data_vencimento
    if body.status is not None:
        task.status = body.status
    if body.relacionado_tipo is not None:
        task.relacionado_tipo = body.relacionado_tipo
    if body.relacionado_id is not None:
        task.relacionado_id = body.relacionado_id
    await db.flush()
    await db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("crm.leads.edit"))],
):
    """Delete task."""
    r = await db.execute(select(Task).where(Task.id == task_id))
    task = r.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.flush()
