"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from jobradar.config import settings

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIM = settings.embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("resume_text", sa.Text),
        sa.Column("work_profile", sa.Text),
        sa.Column("embedding", Vector(DIM)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_profiles_user_id", "user_profiles", ["user_id"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("apply_url", sa.String(1024), nullable=False),
        sa.Column("company", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("locations", sa.String(512)),
        sa.Column("posted_date", sa.String(32)),
        sa.Column("fetched_date", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.Column("description", sa.Text),
        sa.Column("raw", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("work_profile", sa.Text),
        sa.Column("embedding", Vector(DIM)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_jobs_apply_url", "jobs", ["apply_url"], unique=True)
    op.create_index("ix_jobs_company", "jobs", ["company"])
    op.create_index(
        "ix_jobs_embedding_hnsw",
        "jobs",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "job_scores",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("matching_skills", sa.JSON, server_default=sa.text("'[]'::json")),
        sa.Column("gaps", sa.Text),
        sa.Column("info_level", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "job_id", name="uq_job_scores_user_job"),
    )
    op.create_index("ix_job_scores_user_id", "job_scores", ["user_id"])
    op.create_index("ix_job_scores_job_id", "job_scores", ["job_id"])

    op.create_table(
        "trackers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("saved", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("applied_date", sa.Date),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "job_id", name="uq_tracker_user_job"),
    )
    op.create_index("ix_trackers_user_id", "trackers", ["user_id"])
    op.create_index("ix_trackers_job_id", "trackers", ["job_id"])

    op.create_table(
        "scrape_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company", sa.String(128), nullable=False),
        sa.Column("run_date", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.Column("new_jobs", sa.Integer, server_default=sa.text("0")),
        sa.Column("status", sa.String(255), server_default=sa.text("'ok'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_scrape_logs_company", "scrape_logs", ["company"])


def downgrade() -> None:
    op.drop_table("scrape_logs")
    op.drop_table("trackers")
    op.drop_table("job_scores")
    op.drop_index("ix_jobs_embedding_hnsw", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("user_profiles")
    op.drop_table("users")
