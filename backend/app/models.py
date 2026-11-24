"""Relational schema (SQLite + SQLAlchemy). Structured memory, no vector DB."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="You")
    onboarding_stage: Mapped[str] = mapped_column(String(40), default="upload")  # upload|chat|review|done
    llm_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    include_uncertain: Mapped[bool] = mapped_column(Boolean, default=True)
    scheduled_refresh_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="SET NULL"))
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # structured profile (see schemas.ProfileData)
    extraction_method: Mapped[str] = mapped_column(String(40), default="llm")  # llm|heuristic|fixture
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CandidateFact(Base):
    __tablename__ = "candidate_facts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40))  # role|achievement|education|certification|skill|other
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # verbatim CV excerpt
    source: Mapped[str] = mapped_column(String(30), default="cv")  # cv|user|ai_interpretation
    supported: Mapped[bool] = mapped_column(Boolean, default=True)  # evidence found in original CV
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Requirement(Base):
    """Confirmed hard constraints. Only changed by a trusted user action."""
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(30), default="onboarding")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Preference(Base):
    """Confirmed soft preferences and inferred hypotheses (kept separately by status)."""
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    attribute: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(20), default="soft")  # soft|inferred|unknown
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed|proposed|rejected|superseded
    weight: Mapped[float] = mapped_column(Float, default=0.0)  # effect on ranking (-1..1)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    supporting_feedback_ids: Mapped[list] = mapped_column(JSON, default=list)
    contradicting_feedback_ids: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    question: Mapped[str | None] = mapped_column(Text)  # clarification to ask the user, if any
    affects_hard_requirement: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (Index("ix_pref_user_attr", "user_id", "attribute"),)


class OnboardingMessage(Base):
    __tablename__ = "onboarding_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # agent|user
    content: Mapped[str] = mapped_column(Text)
    slot: Mapped[str | None] = mapped_column(String(60))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Employer(Base):
    __tablename__ = "employers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # stable slug
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))  # ashby|greenhouse|unsupported|demo
    board_id: Mapped[str | None] = mapped_column(String(120))
    careers_url: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(100))
    geographic_focus: Mapped[str | None] = mapped_column(String(200))
    nasdaq100: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_ats: Mapped[str | None] = mapped_column(String(60))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_job_count: Mapped[int | None] = mapped_column(Integer)
    uk_jobs_hint: Mapped[int | None] = mapped_column(Integer)  # UK vacancies at registry verification time
    content_hash: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("provider", "employer_id", "external_id", name="uq_job_source"),
        Index("ix_job_active", "active"),
        Index("ix_job_title", "title"),
        Index("ix_job_country", "country"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    employer_id: Mapped[str] = mapped_column(ForeignKey("employers.id"), index=True)
    employer_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    description_text: Mapped[str | None] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)  # sanitised
    location_raw: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(120))
    workplace_type: Mapped[str | None] = mapped_column(String(20))  # remote|hybrid|onsite
    remote_eligibility: Mapped[str | None] = mapped_column(String(120))  # e.g. "UK", "US", "EMEA"
    office_days_per_week: Mapped[float | None] = mapped_column(Float)
    employment_type: Mapped[str | None] = mapped_column(String(30))
    department: Mapped[str | None] = mapped_column(String(200))
    seniority: Mapped[str | None] = mapped_column(String(30))
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    salary_period: Mapped[str | None] = mapped_column(String(20))
    salary_disclosed: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    source_url: Mapped[str | None] = mapped_column(String(800))
    application_url: Mapped[str | None] = mapped_column(String(800))
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    on_call_required: Mapped[bool | None] = mapped_column(Boolean)
    features: Mapped[dict] = mapped_column(JSON, default=dict)  # deterministic role features
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    lifecycle: Mapped[str] = mapped_column(String(30), default="open")  # open|potentially_closed|closed
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    field_provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    also_seen_at: Mapped[list] = mapped_column(JSON, default=list)  # provenance of merged duplicates
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class SearchStrategy(Base):
    __tablename__ = "search_strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    titles: Mapped[list] = mapped_column(JSON, default=list)
    expanded_titles: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    employer_ids: Mapped[list] = mapped_column(JSON, default=list)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_adjustments: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[list] = mapped_column(JSON, default=list)  # human-readable diffs vs previous
    created_by: Mapped[str] = mapped_column(String(30), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id", ondelete="SET NULL"))
    eligibility: Mapped[str] = mapped_column(String(20))  # eligible|ineligible|uncertain
    eligibility_reasons: Mapped[list] = mapped_column(JSON, default=list)
    uncertain_fields: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text)
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    missing_info: Mapped[list] = mapped_column(JSON, default=list)
    semantic: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class DiscoverySession(Base):
    __tablename__ = "discovery_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="preparing")  # preparing|active|completed|empty|failed
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("search_strategies.id", ondelete="SET NULL"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Swipe(Base):
    __tablename__ = "swipes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("discovery_sessions.id", ondelete="SET NULL"))
    decision: Mapped[str] = mapped_column(String(10))  # yes|no|maybe
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)  # replaced by a later decision change
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    swipe_id: Mapped[int | None] = mapped_column(ForeignKey("swipes.id", ondelete="SET NULL"))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    prep_status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending|generating|ready_for_review|needs_correction|failed|cancelled
    app_status: Mapped[str] = mapped_column(String(30), default="not_applied")
    # not_applied|applied|interviewing|rejected|offer_received|withdrawn
    comment: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    generator: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict | None] = mapped_column(JSON)
    requirement_map: Mapped[dict | None] = mapped_column(JSON)
    folder: Mapped[str | None] = mapped_column(String(800))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # cv|cover_letter|job_description
    path: Mapped[str] = mapped_column(String(800))
    version: Mapped[int] = mapped_column(Integer, default=1)
    preview: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="running")
    # running|completed|budget_exhausted|needs_clarification|failed
    model: Mapped[str | None] = mapped_column(String(80))
    planner: Mapped[str] = mapped_column(String(30), default="llm")  # llm|rules
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    strategy_summary: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    result_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    activity: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))  # ok|error|rejected
    result_summary: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # generate_application|discovery|refresh
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_uid: Mapped[str] = mapped_column(String(60), unique=True)
    mode: Mapped[str] = mapped_column(String(20))  # offline|live
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))  # passed|failed|skipped|uncertain
    severity: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
