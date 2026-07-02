from __future__ import annotations

from dataclasses import dataclass

from .settings import Settings


@dataclass(frozen=True)
class BackupRestoreHook:
    env_var: str
    purpose: str
    configured: bool
    required: bool
    reason: str
    reference: str = ""


@dataclass(frozen=True)
class BackupRestoreManifest:
    app_env: str
    object_storage_provider: str
    redis_backup_required: bool
    redis_reason: str
    hooks: tuple[BackupRestoreHook, ...]

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(hook.env_var for hook in self.hooks if hook.required and not hook.configured)

    def as_dict(self, *, show_values: bool = False) -> dict[str, object]:
        hooks: list[dict[str, object]] = []
        for hook in self.hooks:
            item: dict[str, object] = {
                "env_var": hook.env_var,
                "purpose": hook.purpose,
                "configured": hook.configured,
                "required": hook.required,
                "reason": hook.reason,
            }
            if show_values:
                item["reference"] = hook.reference
            hooks.append(item)

        return {
            "app_env": self.app_env,
            "object_storage_provider": self.object_storage_provider,
            "redis_backup_required": self.redis_backup_required,
            "redis_reason": self.redis_reason,
            "missing_required": list(self.missing_required),
            "hooks": hooks,
        }


def build_backup_restore_manifest(settings: Settings) -> BackupRestoreManifest:
    provider = settings.object_storage_provider.lower()
    production_required = settings.is_production
    object_storage_required = settings.is_production and provider != "local"

    hooks = (
        _hook(
            env_var="POSTGRES_BACKUP_HOOK",
            purpose="postgres_backup",
            reference=settings.postgres_backup_hook,
            required=production_required,
            reason="Postgres is the authority for business data, audit, and agent runtime state.",
        ),
        _hook(
            env_var="POSTGRES_RESTORE_HOOK",
            purpose="postgres_restore_drill",
            reference=settings.postgres_restore_hook,
            required=production_required,
            reason="Backups are not production-ready until restore has been exercised.",
        ),
        _hook(
            env_var="OBJECT_STORAGE_BACKUP_HOOK",
            purpose="object_storage_backup",
            reference=settings.object_storage_backup_hook,
            required=object_storage_required,
            reason="Managed object storage contains uploaded files and agent artifacts.",
        ),
        _hook(
            env_var="OBJECT_STORAGE_RESTORE_HOOK",
            purpose="object_storage_restore_drill",
            reference=settings.object_storage_restore_hook,
            required=object_storage_required,
            reason="Object storage retention must be verified through restore drills.",
        ),
    )

    return BackupRestoreManifest(
        app_env=settings.app_env,
        object_storage_provider=provider,
        redis_backup_required=False,
        redis_reason="Redis stores reconstructable runtime controls, locks, cursors, and cache only.",
        hooks=hooks,
    )


def validate_backup_restore_hooks(settings: Settings) -> None:
    manifest = build_backup_restore_manifest(settings)
    if manifest.missing_required:
        missing = ", ".join(manifest.missing_required)
        raise ValueError(f"missing required backup/restore hooks: {missing}")


def _hook(*, env_var: str, purpose: str, reference: str, required: bool, reason: str) -> BackupRestoreHook:
    return BackupRestoreHook(
        env_var=env_var,
        purpose=purpose,
        configured=bool(reference.strip()),
        required=required,
        reason=reason,
        reference=reference,
    )
