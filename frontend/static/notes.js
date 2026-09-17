/* 便签 Web 应用逻辑（vanilla JS，复用 /api/v1/auth/* 与 /api/v1/notes） */
(function () {
  'use strict';

  const API = '/api/v1';
  const TOKEN_KEY = 'sticky_token';
  const COLORS = ['#fff9c4', '#ffd8d8', '#d7f0ff', '#d8f5d8', '#f5e0ff', '#fff0d0'];
  let currentUser = null;
  let noteSeq = 0;

  const $ = (sel) => document.querySelector(sel);

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  /* ---------- API ---------- */
  const token = () => localStorage.getItem(TOKEN_KEY) || '';

  async function api(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (token()) headers['Authorization'] = 'Bearer ' + token();
    let res;
    try {
      res = await fetch(API + path, {
        method, headers, body: body == null ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new Error('网络错误：无法连接服务器');
    }
    let j = null;
    try { j = await res.json(); } catch (e) { /* 非 JSON */ }
    if (!res.ok) {
      const msg = (j && (j.message || j.detail)) || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return j ? j.data : null;
  }

  /* ---------- 视图切换 ---------- */
  function showAuth() {
    $('#auth-view').classList.remove('hidden');
    $('#desk-view').classList.add('hidden');
  }
  function showDesk() {
    $('#auth-view').classList.add('hidden');
    $('#desk-view').classList.remove('hidden');
  }

  /* ---------- 认证 ---------- */
  async function refreshCaptcha() {
    try {
      const data = await api('GET', '/auth/captcha');
      $('#captcha-img').src = 'data:image/png;base64,' + data.image_base64;
      $('#captcha-img').dataset.id = data.captcha_id;
    } catch (e) {
      $('#captcha-img').alt = '验证码加载失败';
    }
  }

  async function doLogin(username, password) {
    const cid = $('#captcha-img').dataset.id;
    if (!cid) throw new Error('请先获取验证码');
    const data = await api('POST', '/auth/login', {
      username, password,
      captcha_id: cid, captcha_code: $('#login-form').captcha_code.value.trim(),
    });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    return data;
  }

  async function doRegister(username, password, display_name) {
    const data = await api('POST', '/auth/register', { username, password, display_name });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    return data;
  }

  async function fetchMe() {
    return api('GET', '/auth/me');
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    currentUser = null;
    $('#board').innerHTML = '';
    $('#empty-hint').classList.add('hidden');
    showAuth();
  }

  /* ---------- 便签桌 ---------- */
  function makeNoteEl(note) {
    const tpl = $('#note-tpl');
    const el = tpl.content.cloneNode(true).querySelector('.note');
    el.dataset.id = note.id || ('tmp-' + (++noteSeq));
    el.style.background = note.color || COLORS[0];
    el.style.left = (note.pos_x || 0) + 'px';
    el.style.top = (note.pos_y || 0) + 'px';
    el.querySelector('.note-title').value = note.title || '';
    el.querySelector('.note-content').value = note.content || '';

    // 删除
    el.querySelector('.note-del').addEventListener('click', () => {
      if (!confirm('确认关闭这张便签？')) return;
      const id = el.dataset.id;
      if (!id.startsWith('tmp-')) {
        api('DELETE', '/notes/' + id).catch(() => {});
      }
      el.remove();
      refreshEmptyHint();
    });

    // 改色
    el.querySelectorAll('.dot').forEach((d) => {
      d.addEventListener('click', () => {
        const c = d.dataset.c;
        el.style.background = c;
        saveNoteSoon(idOf(el), true);
      });
    });

    // 编辑自动保存（防抖）
    const title = el.querySelector('.note-title');
    const content = el.querySelector('.note-content');
    title.addEventListener('input', () => saveNoteSoon(idOf(el), false));
    content.addEventListener('input', () => saveNoteSoon(idOf(el), false));

    // 拖动（按拖动条）
    el.querySelector('.note-drag').addEventListener('pointerdown', (ev) => startDrag(ev, el));
    return el;
  }

  function idOf(el) { return el.dataset.id; }

  function saveNoteSoon(id, immediate) {
    const el = document.querySelector(`.note[data-id="${CSS.escape(id)}"]`);
    if (!el) return;
    clearTimeout(el._t);
    const doSave = () => {
      if (id.startsWith('tmp-')) return; // 未入库存的新建草稿
      api('PUT', '/notes/' + id, {
        title: el.querySelector('.note-title').value,
        content: el.querySelector('.note-content').value,
        color: el.style.background,
        pos_x: parseInt(el.style.left, 10) || 0,
        pos_y: parseInt(el.style.top, 10) || 0,
      }).catch(() => {});
    };
    if (immediate) { clearTimeout(el._t); doSave(); }
    else el._t = setTimeout(doSave, 600);
  }

  function startDrag(ev, el) {
    ev.preventDefault();
    const board = $('#board');
    const startX = ev.clientX, startY = ev.clientY;
    const left = parseInt(el.style.left, 10) || 0;
    const top = parseInt(el.style.top, 10) || 0;
    el.classList.add('dragging');

    const move = (e) => {
      let x = left + (e.clientX - startX);
      let y = top + (e.clientY - startY);
      x = Math.max(0, x); y = Math.max(0, y);
      el.style.left = x + 'px';
      el.style.top = y + 'px';
    };
    const up = () => {
      el.classList.remove('dragging');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      saveNoteSoon(idOf(el), true); // 落位即存档，下次打开仍在原处
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  async function refreshDesk() {
    const notes = (await api('GET', '/notes')) || [];
    // 先清除非草稿元素（草稿留在原处防误毁）
    document.querySelectorAll('.note:not([data-id^="tmp-"])').forEach((n) => n.remove());
    const board = $('#board');
    notes.forEach((note) => {
      const el = makeNoteEl(note);
      board.appendChild(el);
    });
    refreshEmptyHint();
  }

  function refreshEmptyHint() {
    const has = $('#board').querySelectorAll('.note').length > 0;
    $('#empty-hint').classList.toggle('hidden', has);
  }

  async function createNote() {
    const board = $('#board');
    const w = 260, h = 180;
    const vw = board.clientWidth || 800, vh = board.clientHeight || 600;
    const x = Math.max(0, Math.round((vw - w) / 2 + (Math.random() - 0.5) * 140));
    const y = Math.max(0, Math.round((vh - h) / 2 + (Math.random() - 0.5) * 120));
    const color = COLORS[Math.floor(Math.random() * COLORS.length)];
    const note = await api('POST', '/notes', {
      title: '', content: '', color, pos_x: x, pos_y: y,
    });
    const el = makeNoteEl(note);
    board.appendChild(el);
    refreshEmptyHint();
    el.querySelector('.note-content').focus();
    return el;
  }

  /* ---------- 绑定事件 ---------- */
  function bindEvents() {
    // 登录页验证码
    $('#captcha-refresh').addEventListener('click', (e) => { e.preventDefault(); refreshCaptcha(); });
    $('#captcha-img').addEventListener('click', refreshCaptcha);

    // 标签切换
    $('#auth-tabs').addEventListener('click', (e) => {
      const t = e.target.closest('.tab');
      if (!t) return;
      document.querySelectorAll('#auth-tabs .tab').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      $('#login-form').classList.toggle('hidden', t.dataset.tab !== 'login');
      $('#register-form').classList.toggle('hidden', t.dataset.tab !== 'register');
      $('#form-error').classList.add('hidden');
    });

    $('#login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        await doLogin(f.username.value.trim(), f.password.value);
        await enterDesk();
      } catch (err) {
        showError(err.message);
        refreshCaptcha(); // 验证码失败需重新获取
      }
    });

    $('#register-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        await doRegister(f.username.value.trim(), f.password.value, (f.display_name.value || '').trim());
        await enterDesk();
      } catch (err) {
        showError(err.message);
      }
    });

    $('#btn-logout').addEventListener('click', logout);
    $('#btn-new').addEventListener('click', () => createNote().catch(showError));
    $('#btn-admin').addEventListener('click', () => { window.open('/admin/', '_blank'); });
  }

  function showError(msg) {
    const box = $('#form-error');
    box.textContent = msg;
    box.classList.remove('hidden');
  }

  async function enterDesk() {
    currentUser = await fetchMe();
    $('#user-chip').textContent = currentUser.display_name || currentUser.username;
    $('#btn-admin').classList.toggle('hidden', !hasAdminPerm(currentUser));
    showDesk();
    await refreshDesk();
  }

  function hasAdminPerm(user) {
    return !!(user && (user.is_superuser ||
      (user.permissions || []).some((p) => p.startsWith('sys:'))));
  }

  /* ---------- 启动 ---------- */
  async function boot() {
    bindEvents();
    if (token()) {
      try {
        await enterDesk();
        return;
      } catch (e) {
        localStorage.removeItem(TOKEN_KEY); // 令牌失效 → 回登录
      }
    }
    showAuth();
    refreshCaptcha();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
