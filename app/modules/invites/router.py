from __future__ import annotations

import json
from html import escape

from fastapi import Depends, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_service_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.settings import Settings
from ...infrastructure.db import get_session
from ..auth import AuthSessionRepository, AuthSessionService, ServiceClient
from .repository import InviteCodeRepository
from .schemas import InviteCodeCreate, InviteCodeListResponse, InviteCodeRead
from .service import InviteCodeService, normalize_invite_code


router = SurfaceAPIRouter(
    prefix="/admin/invite-codes",
    tags=["admin-invite-codes"],
    api_surface_metadata=api_surface("admin_ops_api", owner="auth", clients=["admin-console"]),
)


def get_invite_code_service(session: AsyncSession = Depends(get_session)) -> InviteCodeService:
    return InviteCodeService(
        repository=InviteCodeRepository(session),
        session_revoker=AuthSessionService(repository=AuthSessionRepository(session)),
    )


@router.get("/ui", response_class=HTMLResponse)
async def invite_codes_admin_ui(request: Request) -> HTMLResponse:
    return HTMLResponse(_admin_page_html(request.app.state.settings))


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
    offset: int = Query(default=0, ge=0),
    _service_client: ServiceClient = Depends(require_service_client),
    service: InviteCodeService = Depends(get_invite_code_service),
) -> InviteCodeListResponse:
    page = await service.list_invite_codes(limit=limit, offset=offset)
    return InviteCodeListResponse(
        items=[InviteCodeRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


@router.post("/{code}/disable", response_model=InviteCodeRead)
async def disable_invite_code(
    code: str,
    _service_client: ServiceClient = Depends(require_service_client),
    service: InviteCodeService = Depends(get_invite_code_service),
) -> InviteCodeRead:
    invite_code = await service.disable_invite_code(code=normalize_invite_code(code))
    return InviteCodeRead.model_validate(invite_code)


def _admin_page_html(settings: Settings) -> str:
    title = escape("Momcozy Invitation Codes")
    service_key_json = json.dumps(settings.service_api_key).replace("</", "<\\/")
    return (
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #fbf7f5;
      --card: #fff;
      --text: #332530;
      --muted: #7d6a75;
      --border: #eadde2;
      --primary: #aa6579;
      --primary-dark: #765f6b;
      --soft: #f7eef2;
      --ok-bg: #f1faf5;
      --ok: #2f7a55;
      --warn-bg: #fff7e8;
      --warn: #9a641c;
      --danger-bg: #fff0f1;
      --danger: #b04151;
    }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }
    main { max-width: 1120px; margin: 38px auto; padding: 0 20px 48px; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
    h1 { font-size: 28px; margin: 0; }
    h2 { font-size: 20px; margin: 0 0 16px; }
    section {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 22px;
      margin-bottom: 18px;
      box-shadow: 0 12px 32px rgba(77, 45, 60, .08);
    }
    label { display: block; font-weight: 800; margin: 12px 0 7px; }
    input {
      width: 100%;
      border: 1px solid #d9c8cf;
      border-radius: 12px;
      padding: 13px 14px;
      font-size: 15px;
      outline: none;
      background: #fff;
    }
    input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(170, 101, 121, .12); }
    button {
      border: 0;
      border-radius: 999px;
      background: var(--primary);
      color: white;
      padding: 11px 18px;
      font-size: 14px;
      font-weight: 800;
      cursor: pointer;
      white-space: nowrap;
    }
    button.secondary { background: var(--primary-dark); }
    button.ghost { color: var(--primary-dark); background: var(--soft); }
    button.danger { background: var(--danger); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 16px; }
    .hint { color: var(--muted); font-size: 13px; margin: 10px 0 0; }
    .status {
      min-height: 42px;
      display: flex;
      align-items: center;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--soft);
      padding: 10px 13px;
      color: var(--muted);
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 18px;
    }
    .status.ok { background: var(--ok-bg); color: var(--ok); border-color: #cfeadd; }
    .status.error { background: var(--danger-bg); color: var(--danger); border-color: #f1cbd2; }
    .table-card { padding: 0; overflow: hidden; }
    .table-header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 20px 22px;
      border-bottom: 1px solid var(--border);
    }
    .table-wrap { width: 100%; overflow-x: auto; }
    table { width: 100%; min-width: 1180px; border-collapse: collapse; }
    th, td { padding: 13px 14px; border-bottom: 1px solid #f0e6ea; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }
    td { font-size: 14px; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 800; }
    .muted { color: var(--muted); }
    .truncate {
      display: inline-block;
      max-width: 150px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      vertical-align: bottom;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 800;
      background: var(--soft);
      color: var(--muted);
    }
    .badge.available, .badge.bound { background: var(--ok-bg); color: var(--ok); }
    .badge.unavailable { background: var(--danger-bg); color: var(--danger); }
    .badge.unbound { background: var(--warn-bg); color: var(--warn); }
    .empty { text-align: center; color: var(--muted); padding: 24px !important; }
    .pagination {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      padding: 16px 22px;
      border-top: 1px solid var(--border);
    }
    .page-summary { color: var(--muted); font-size: 13px; font-weight: 700; margin-right: auto; }
    @media (max-width: 760px) {
      main { margin-top: 24px; padding: 0 14px 32px; }
      header, .table-header { align-items: flex-start; flex-direction: column; }
      .form-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>__TITLE__</h1>
      <button class="ghost" onclick="loadInviteCodes()">Refresh list</button>
    </header>
    <div id="status" class="status">Admin credentials are injected by the backend. The invitation code list loads automatically.</div>
    <section>
      <h2>Create invitation code</h2>
      <div class="form-grid">
        <div>
          <label for="label">Notes</label>
          <input id="label" placeholder="For example: Alice beta test" />
        </div>
        <div>
          <label for="assignedTo">Assigned to</label>
          <input id="assignedTo" placeholder="Email, name, or notes (optional)" />
        </div>
      </div>
      <div class="actions">
        <button id="createButton" onclick="createInviteCode()">Create invitation code</button>
      </div>
      <p class="hint">New codes appear at the top of the table below.</p>
    </section>
    <section class="table-card">
      <div class="table-header">
        <div>
          <h2>Invitation codes</h2>
          <p class="hint">Disabling a bound code also signs the user out.</p>
        </div>
        <button class="secondary" onclick="loadInviteCodes()">Refresh list</button>
      </div>
      <div class="table-wrap">
        <table aria-label="Invitation codes">
          <thead>
            <tr>
              <th>Invitation code</th>
              <th>Bound</th>
              <th>Available</th>
              <th>Notes</th>
              <th>Assigned to</th>
              <th>Bound device</th>
              <th>Bound user</th>
              <th>Use count</th>
              <th>Expires</th>
              <th>Created</th>
              <th>Disabled</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="inviteRows">
            <tr><td class="empty" colspan="12">Loading…</td></tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span id="pageSummary" class="page-summary">-</span>
        <button id="prevPageButton" class="ghost" onclick="previousPage()">Previous page</button>
        <button id="nextPageButton" class="ghost" onclick="nextPage()">Next page</button>
      </div>
    </section>
  </main>
  <script>
    const SERVICE_KEY = __SERVICE_KEY__;
    const state = { items: [], total: 0, limit: 20, offset: 0, hasMore: false };

    function setStatus(message, kind = '') {
      const node = document.getElementById('status');
      node.textContent = message;
      node.className = 'status' + (kind ? ' ' + kind : '');
    }

    function apiHeaders() {
      if (!SERVICE_KEY) throw new Error('SERVICE_API_KEY is not configured. The admin API is unavailable.');
      return { 'Content-Type': 'application/json', 'X-Service-Key': SERVICE_KEY };
    }

    async function request(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { ...apiHeaders(), ...(options.headers || {}) }
      });
      const text = await response.text();
      let payload = {};
      try {
        payload = text ? JSON.parse(text) : {};
      } catch (_) {
        payload = { error: { message: text || 'Could not parse the API response' } };
      }
      if (!response.ok) throw new Error(payload.error ? payload.error.message : text);
      return payload;
    }

    function text(value, fallback = '-') {
      const normalized = String(value || '').trim();
      return normalized || fallback;
    }

    function shortText(value) {
      const normalized = text(value);
      if (normalized === '-') return normalized;
      return normalized.length > 18 ? normalized.slice(0, 8) + '...' + normalized.slice(-6) : normalized;
    }

    function formatTime(value) {
      if (!value) return '-';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString('en-US', { hour12: false });
    }

    function isBound(item) {
      if (typeof item.is_bound === 'boolean') return item.is_bound;
      return Boolean(text(item.bound_device_id, '') || text(item.bound_user_id, ''));
    }

    function isAvailable(item) {
      if (typeof item.is_available === 'boolean') return item.is_available;
      if (item.status !== 'active') return false;
      if (!item.expires_at) return true;
      return new Date(item.expires_at).getTime() > Date.now();
    }

    function renderPagination() {
      const start = state.total === 0 ? 0 : state.offset + 1;
      const end = Math.min(state.offset + state.items.length, state.total);
      document.getElementById('pageSummary').textContent = `Showing ${start}-${end} of ${state.total}`;
      document.getElementById('prevPageButton').disabled = state.offset <= 0;
      document.getElementById('nextPageButton').disabled = !state.hasMore;
    }

    function renderInviteCodes() {
      const body = document.getElementById('inviteRows');
      body.innerHTML = '';
      if (!state.items.length) {
        const row = document.createElement('tr');
        row.innerHTML = '<td class="empty" colspan="12">No invitation codes</td>';
        body.appendChild(row);
        renderPagination();
        return;
      }

      for (const item of state.items) {
        const bound = isBound(item);
        const available = isAvailable(item);
        const row = document.createElement('tr');
        row.dataset.code = item.code;
        row.innerHTML = `
          <td><code></code></td>
          <td><span class="badge binding"></span></td>
          <td><span class="badge availability"></span></td>
          <td><span class="truncate label"></span></td>
          <td><span class="truncate assigned"></span></td>
          <td><span class="truncate device"></span></td>
          <td><span class="truncate user"></span></td>
          <td class="used"></td>
          <td class="expires"></td>
          <td class="created"></td>
          <td class="disabled"></td>
          <td class="action"></td>
        `;
        row.querySelector('code').textContent = item.code;
        const bindingBadge = row.querySelector('.binding');
        bindingBadge.textContent = bound ? 'Bound' : 'Not bound';
        bindingBadge.classList.add(bound ? 'bound' : 'unbound');
        const availabilityBadge = row.querySelector('.availability');
        availabilityBadge.textContent = available ? 'Available' : 'Disable';
        availabilityBadge.classList.add(available ? 'available' : 'unavailable');
        row.querySelector('.label').textContent = text(item.label);
        row.querySelector('.label').title = text(item.label);
        row.querySelector('.assigned').textContent = text(item.assigned_to);
        row.querySelector('.assigned').title = text(item.assigned_to);
        row.querySelector('.device').textContent = shortText(item.bound_device_id);
        row.querySelector('.device').title = text(item.bound_device_id);
        row.querySelector('.user').textContent = shortText(item.bound_user_id);
        row.querySelector('.user').title = text(item.bound_user_id);
        row.querySelector('.used').textContent = String(item.used_count || 0);
        row.querySelector('.expires').textContent = formatTime(item.expires_at);
        row.querySelector('.created').textContent = formatTime(item.created_at);
        row.querySelector('.disabled').textContent = formatTime(item.disabled_at);

        const action = row.querySelector('.action');
        if (item.status === 'active') {
          const button = document.createElement('button');
          button.className = 'danger';
          button.textContent = isBound(item) ? 'Sign out user' : 'Disable';
          button.onclick = () => disableInviteCode(item.code);
          action.appendChild(button);
        } else {
          action.innerHTML = '<span class="muted">No action available</span>';
        }
        body.appendChild(row);
      }
      renderPagination();
    }

    function upsertInviteCode(item, { prepend = false } = {}) {
      const existingIndex = state.items.findIndex((current) => current.code === item.code);
      if (existingIndex >= 0 && !prepend) state.items[existingIndex] = item;
      else {
        if (existingIndex >= 0) state.items.splice(existingIndex, 1);
        if (prepend) state.items.unshift(item);
        else state.items.push(item);
      }
      if (prepend && state.items.length > state.limit) state.items.pop();
      renderInviteCodes();
    }

    async function loadInviteCodes(offset = state.offset) {
      try {
        setStatus('Loading invitation codes…');
        const nextOffset = Math.max(0, offset);
        const payload = await request('/v1/admin/invite-codes?limit=' + state.limit + '&offset=' + nextOffset);
        state.items = payload.items || [];
        state.total = payload.total || 0;
        state.limit = payload.limit || state.limit;
        state.offset = payload.offset || 0;
        state.hasMore = Boolean(payload.has_more);
        renderInviteCodes();
        setStatus('Invitation codes updated.', 'ok');
      } catch (error) {
        setStatus('Could not load: ' + error.message, 'error');
        renderInviteCodes();
      }
    }

    function previousPage() {
      if (state.offset <= 0) return;
      loadInviteCodes(Math.max(0, state.offset - state.limit));
    }

    function nextPage() {
      if (!state.hasMore) return;
      loadInviteCodes(state.offset + state.limit);
    }

    async function createInviteCode() {
      const button = document.getElementById('createButton');
      try {
        button.disabled = true;
        setStatus('Creating invitation code…');
        const payload = await request('/v1/admin/invite-codes', {
          method: 'POST',
          body: JSON.stringify({
            label: document.getElementById('label').value,
            assigned_to: document.getElementById('assignedTo').value
          })
        });
        document.getElementById('label').value = '';
        document.getElementById('assignedTo').value = '';
        if (state.offset === 0) {
          state.total += 1;
          upsertInviteCode(payload, { prepend: true });
          state.hasMore = state.offset + state.items.length < state.total;
          setStatus('Created: ' + payload.code, 'ok');
        } else {
          await loadInviteCodes(0);
          setStatus('Created: ' + payload.code + '. Returned to the first page.', 'ok');
        }
      } catch (error) {
        setStatus('Could not create: ' + error.message, 'error');
      } finally {
        button.disabled = false;
      }
    }

    async function disableInviteCode(code) {
      const existing = state.items.find((item) => item.code === code);
      const willKickUser = existing ? isBound(existing) : false;
      const prompt = willKickUser ? 'Disable ' + code + '? The bound user will be signed out.' : 'Disable ' + code + '?';
      if (!confirm(prompt)) return;
      try {
        setStatus('Disabling: ' + code);
        const payload = await request('/v1/admin/invite-codes/' + encodeURIComponent(code) + '/disable', { method: 'POST', body: '{}' });
        upsertInviteCode(payload);
        const message = isBound(payload) ? 'Invitation code disabled and bound user signed out: ' : 'Invitation code disabled: ';
        setStatus(message + payload.code, 'ok');
      } catch (error) {
        setStatus('Could not disable: ' + error.message, 'error');
      }
    }

    loadInviteCodes();
  </script>
</body>
</html>"""
        .replace("__TITLE__", title)
        .replace("__SERVICE_KEY__", service_key_json)
    )
