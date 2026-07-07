from __future__ import annotations

from html import escape

from fastapi import Depends, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_service_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..auth import ServiceClient
from .repository import InviteCodeRepository
from .schemas import InviteCodeCreate, InviteCodeListResponse, InviteCodeRead
from .service import InviteCodeService, normalize_invite_code


router = SurfaceAPIRouter(
    prefix="/admin/invite-codes",
    tags=["admin-invite-codes"],
    api_surface_metadata=api_surface("admin_ops_api", owner="auth", clients=["admin-console"]),
)


def get_invite_code_service(session: AsyncSession = Depends(get_session)) -> InviteCodeService:
    return InviteCodeService(repository=InviteCodeRepository(session))


@router.get("/ui", response_class=HTMLResponse)
async def invite_codes_admin_ui() -> HTMLResponse:
    return HTMLResponse(_admin_page_html())


@router.post("", response_model=InviteCodeRead, status_code=status.HTTP_201_CREATED)
async def create_invite_code(
    payload: InviteCodeCreate,
    service_client: ServiceClient = Depends(require_service_client),
    service: InviteCodeService = Depends(get_invite_code_service),
) -> InviteCodeRead:
    invite_code = await service.create_invite_code(
        code=payload.code,
        label=payload.label,
        assigned_to=payload.assigned_to,
        expires_at=payload.expires_at,
        actor_service=service_client.name,
    )
    return InviteCodeRead.model_validate(invite_code)


@router.get("", response_model=InviteCodeListResponse)
async def list_invite_codes(
    limit: int = Query(default=50, ge=1, le=100),
    _service_client: ServiceClient = Depends(require_service_client),
    service: InviteCodeService = Depends(get_invite_code_service),
) -> InviteCodeListResponse:
    invite_codes = await service.list_invite_codes(limit=limit)
    return InviteCodeListResponse(items=[InviteCodeRead.model_validate(item) for item in invite_codes])


@router.post("/{code}/disable", response_model=InviteCodeRead)
async def disable_invite_code(
    code: str,
    _service_client: ServiceClient = Depends(require_service_client),
    service: InviteCodeService = Depends(get_invite_code_service),
) -> InviteCodeRead:
    invite_code = await service.disable_invite_code(code=normalize_invite_code(code))
    return InviteCodeRead.model_validate(invite_code)


def _admin_page_html() -> str:
    title = escape("MomCozy 邀请码管理")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #fbf7f5; color: #332530; }}
    main {{ max-width: 760px; margin: 40px auto; padding: 0 20px; }}
    section {{ background: white; border: 1px solid #eadde2; border-radius: 14px; padding: 20px; margin-bottom: 18px; box-shadow: 0 12px 32px rgba(77, 45, 60, .08); }}
    h1 {{ font-size: 28px; margin: 0 0 20px; }}
    h2 {{ font-size: 18px; margin: 0 0 14px; }}
    label {{ display: block; font-weight: 700; margin: 12px 0 6px; }}
    input {{ width: 100%; box-sizing: border-box; border: 1px solid #d9c8cf; border-radius: 10px; padding: 12px; font-size: 15px; }}
    button {{ border: 0; border-radius: 999px; background: #aa6579; color: white; padding: 11px 18px; font-size: 15px; font-weight: 700; margin-top: 14px; cursor: pointer; }}
    button.secondary {{ background: #6f5964; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f7eef2; border-radius: 10px; padding: 12px; min-height: 42px; }}
    .hint {{ color: #7d6a75; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <section>
      <h2>管理员凭证</h2>
      <label for="serviceKey">X-Service-Key</label>
      <input id="serviceKey" type="password" autocomplete="off" placeholder="输入 SERVICE_API_KEY" />
      <p class="hint">凭证只保存在当前浏览器页面内，用于调用受保护的管理 API。</p>
    </section>
    <section>
      <h2>创建邀请码</h2>
      <label for="label">备注</label>
      <input id="label" placeholder="例如 Alice 内测" />
      <label for="assignedTo">分发对象</label>
      <input id="assignedTo" placeholder="邮箱、姓名或备注，可留空" />
      <button onclick="createInviteCode()">创建邀请码</button>
    </section>
    <section>
      <h2>禁用邀请码</h2>
      <label for="disableCode">邀请码</label>
      <input id="disableCode" placeholder="例如 MCZ-ABCD-2345" />
      <button class="secondary" onclick="disableInviteCode()">禁用指定邀请码</button>
    </section>
    <section>
      <h2>结果</h2>
      <pre id="result">等待操作...</pre>
    </section>
  </main>
  <script>
    function serviceKey() {{
      return document.getElementById('serviceKey').value.trim();
    }}
    function show(value) {{
      document.getElementById('result').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }}
    async function request(path, options) {{
      const key = serviceKey();
      if (!key) throw new Error('请先输入 X-Service-Key');
      const response = await fetch(path, {{
        ...options,
        headers: {{
          'Content-Type': 'application/json',
          'X-Service-Key': key,
          ...(options.headers || {{}})
        }}
      }});
      const text = await response.text();
      const payload = text ? JSON.parse(text) : {{}};
      if (!response.ok) throw new Error(payload.error ? payload.error.message : text);
      return payload;
    }}
    async function createInviteCode() {{
      try {{
        const payload = await request('/v1/admin/invite-codes', {{
          method: 'POST',
          body: JSON.stringify({{
            label: document.getElementById('label').value,
            assigned_to: document.getElementById('assignedTo').value
          }})
        }});
        show('创建成功：' + payload.code + '\\n\\n' + JSON.stringify(payload, null, 2));
      }} catch (error) {{
        show('创建失败：' + error.message);
      }}
    }}
    async function disableInviteCode() {{
      try {{
        const code = encodeURIComponent(document.getElementById('disableCode').value.trim());
        const payload = await request('/v1/admin/invite-codes/' + code + '/disable', {{ method: 'POST', body: '{{}}' }});
        show('禁用成功：' + payload.code + '\\n\\n' + JSON.stringify(payload, null, 2));
      }} catch (error) {{
        show('禁用失败：' + error.message);
      }}
    }}
  </script>
</body>
</html>"""
