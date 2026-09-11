"""Verified accounts, purpose-bound email challenges and erasure requests."""
from alembic import op
import sqlalchemy as sa

revision = "20260910_0017"
down_revision = "20260909_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users u SET email = lower(trim(i.email)) FROM auth_identities i WHERE i.user_id = u.id AND i.provider = 'email' AND i.email <> ''")
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    # Historical passwords did not establish mailbox ownership. Do not backfill a false verification.
    op.execute("UPDATE users SET status = 'email_unverified' WHERE email IS NOT NULL AND status = 'active'")
    op.execute("UPDATE device_sessions SET status = 'revoked', revoked_at = now() WHERE user_id IN (SELECT id FROM users WHERE status = 'email_unverified')")
    op.execute("UPDATE refresh_tokens SET status = 'revoked', revoked_at = now() WHERE session_id IN (SELECT id FROM device_sessions WHERE status = 'revoked')")
    op.alter_column("auth_identities", "subject", type_=sa.String(320), existing_type=sa.String(255), existing_nullable=False)
    op.add_column("auth_identities", sa.Column("display_name", sa.String(255), server_default="", nullable=False))
    op.add_column("auth_identities", sa.Column("avatar_url", sa.String(1024), server_default="", nullable=False))
    op.create_table("auth_email_challenges",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "purpose", name="uq_auth_email_challenge_user_purpose"))
    op.create_table("account_deletion_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending_erasure", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", name="uq_account_deletion_requests_user_id"))

    op.create_table("auth_email_deliveries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("encrypted_message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_email_deliveries_status", "auth_email_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_auth_email_deliveries_status", table_name="auth_email_deliveries")
    op.drop_table("auth_email_deliveries")
    op.drop_table("account_deletion_requests")
    op.drop_table("auth_email_challenges")
    op.drop_column("auth_identities", "avatar_url")
    op.drop_column("auth_identities", "display_name")
    # Retain wider identity subjects; do not truncate valid email addresses on rollback.
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email")
