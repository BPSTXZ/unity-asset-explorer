import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const PAGE_SIZE = 200;
const EXAMPLE_PATH = 'H:\\87VR游戏大厅\\Romantic.Escapades.v2.0.3\\Romantic Escapades';

const TYPE_ZH = {
  Character: '角色', Mesh: '模型', Texture2D: '贴图', Sprite: '精灵图', AnimationClip: '动画片段',
  AudioClip: '音频片段', Material: '材质', Shader: '着色器', TextAsset: '文本资源',
  Font: '字体', Cubemap: '立方体贴图', VideoClip: '视频片段',
};
const zh = (t) => TYPE_ZH[t] || t;
const IMAGE_TYPES = ['Texture2D', 'Sprite', 'Cubemap'];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  detected: null, typeFilter: null, search: '', total: 0, items: [], page: 0,
  sortKey: null, sortDir: 1,
  selected: new Set(), current: null, scanTimer: null, exportTimer: null, exportIds: [],
  favMode: false, favType: null, favorites: [], favIds: new Set(), favPending: null, favRenameKey: null,
};

function toast(msg, err = false) {
  const t = $('toast');
  t.textContent = msg;
  t.className = err ? 'err' : '';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), 4000);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r;
}
const postJson = (path, body) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
function fmtSize(n) {
  if (n > 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

// ---------------- 可拖拽分栏 ----------------
function initDividers() {
  const layout = $('layout');
  const savedSide = localStorage.getItem('ae_sideW');
  const savedPrev = localStorage.getItem('ae_prevW');
  if (savedSide) layout.style.setProperty('--sideW', savedSide);
  if (savedPrev) layout.style.setProperty('--prevW', savedPrev);
  else layout.style.setProperty('--prevW', Math.max(380, Math.round(innerWidth * 0.44)) + 'px');

  function bind(el, varName, sign, min, maxFn) {
    el.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      el.classList.add('active');
      el.setPointerCapture(e.pointerId);
      const start = e.clientX;
      const cur = parseFloat(getComputedStyle(layout).getPropertyValue(varName)) || 0;
      const move = (ev) => {
        const delta = (ev.clientX - start) * sign;
        const val = Math.min(maxFn(), Math.max(min, cur + delta));
        layout.style.setProperty(varName, val + 'px');
        viewer?.resize?.();
      };
      const up = (ev) => {
        el.classList.remove('active');
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', up);
        localStorage.setItem('ae_' + varName.slice(2), getComputedStyle(layout).getPropertyValue(varName).trim());
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', up);
    });
  }
  bind($('divLeft'), '--sideW', +1, 150, () => 400);
  bind($('divRight'), '--prevW', -1, 300, () => innerWidth - 420);
  addEventListener('resize', () => viewer?.resize?.());
}

// ---------------- 文件夹选择 ----------------
let folderCur = null, folderSel = null;
async function openFolder(startPath) {
  $('folderDialog').classList.remove('hidden');
  folderSel = null;
  await loadFolder(startPath || null);
}
async function loadFolder(path) {
  const d = await (await api('/api/fs/list' + (path ? '?path=' + encodeURIComponent(path) : ''))).json();
  folderCur = d.path;
  $('folderPath').textContent = d.path || '此电脑';
  $('folderUp').disabled = !d.parent;
  const ul = $('folderList');
  ul.innerHTML = '';
  if (!d.entries.length) ul.innerHTML = '<li class="empty">（空目录）</li>';
  for (const e of d.entries) {
    const li = document.createElement('li');
    const ico = e.type === 'drive' ? '💽' : e.type === 'file' ? '📦' : '📁';
    li.innerHTML = `<span class="ico">${ico}</span><span>${esc(e.name)}</span>`;
    li.ondblclick = () => {
      if (e.type === 'file') chooseFolderEntry(e.path);
      else loadFolder(e.path);
    };
    li.onclick = () => {
      folderSel = e.path;
      [...ul.children].forEach(x => x.classList.remove('sel'));
      li.classList.add('sel');
    };
    ul.appendChild(li);
  }
}
$('btnBrowse').onclick = () => openFolder($('pathInput').value.trim() || null);
$('folderUp').onclick = () => loadFolder(folderCur ? parentOf(folderCur) : null);
$('folderDrives').onclick = () => loadFolder(null);
$('folderCancel').onclick = () => $('folderDialog').classList.add('hidden');
function chooseFolderEntry(p) {
  if (!p) return toast('请选择一个文件夹或文件', true);
  $('pathInput').value = p;
  $('folderDialog').classList.add('hidden');
  $('btnDetect').click();
}
$('folderChoose').onclick = () => chooseFolderEntry(folderSel || folderCur);
function parentOf(p) {
  const i = Math.max(p.lastIndexOf('\\'), p.lastIndexOf('/'));
  if (i <= 2) return null;
  return p.slice(0, i);
}

// ---------------- 识别 / 扫描 ----------------
function setProject(path) {
  const row = $('projRow');
  if (!path) { row.classList.add('hidden'); return; }
  const el = $('projName');
  const name = path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
  el.textContent = '📦 ' + name;
  el.title = path;
  row.classList.remove('hidden');
}
function showEngineBadge(d) {
  const badge = $('engineBadge');
  const parts = [d.engine_label];
  if (d.unity_version) parts.push(d.unity_version);
  if (d.scripting_backend && d.scripting_backend !== 'unknown') parts.push(d.scripting_backend);
  if (d.asset_files) parts.push(`${d.asset_files.length} 个资源文件`);
  else if (d.asset_file_count != null) parts.push(`${d.asset_file_count} 个资源文件`);
  badge.textContent = parts.join(' · ');
  badge.classList.remove('hidden');
}
async function doDetect() {
  const path = $('pathInput').value.trim();
  if (!path) return toast('请输入或选择路径', true);
  try {
    const d = await (await postJson('/api/detect', { path })).json();
    state.detected = d;
    if (d.engine) {
      showEngineBadge(d);
      $('btnScan').disabled = !d.supported;
      if (!d.supported) toast(d.notes?.join('；') || '该引擎暂不支持解析', true);
      else if (d.notes?.length) toast(d.notes.join('；'));
    } else {
      $('engineBadge').classList.add('hidden');
      $('btnScan').disabled = true;
      toast(d.error || '识别失败', true);
    }
  } catch (e) { toast(e.message, true); }
}
$('btnDetect').onclick = doDetect;
$('btnExample').onclick = () => { $('pathInput').value = EXAMPLE_PATH; doDetect(); };

$('btnScan').onclick = async () => {
  try {
    await postJson('/api/scan', {});
    $('btnScan').disabled = true;
    $('scanProgress').classList.remove('hidden');
    state.selected.clear();
    setProject(state.detected?.root || $('pathInput').value.trim());
    pollScan();
  } catch (e) { toast(e.message, true); }
};

function pollScan() {
  clearTimeout(state.scanTimer);
  state.scanTimer = setTimeout(async () => {
    const st = await (await api('/api/scan/status')).json();
    $('btnGallery').disabled = !st.asset_count;
    const pct = st.total_files ? Math.round(st.done_files / st.total_files * 100) : 0;
    const p = $('scanProgress');
    p.querySelector('.bar').style.width = pct + '%';
    const pauseBtn = $('btnScanPause');
    if (st.state === 'running' || st.state === 'paused') {
      pauseBtn.classList.remove('hidden');
      pauseBtn.disabled = false;
      pauseBtn.textContent = st.state === 'paused' ? '继续' : '暂停';
      p.querySelector('.label').textContent = st.state === 'paused'
        ? `已暂停 ${st.done_files}/${st.total_files} · ${st.asset_count}`
        : `扫描中 ${st.done_files}/${st.total_files} · ${st.asset_count}`;
      state._pollN = (state._pollN || 0) + 1;
      if (st.asset_count && state._pollN % 4 === 0) {
        renderTypeList(st.type_counts); loadAssets(); $('btnExportAll').disabled = false;
      }
      return pollScan();
    }
    pauseBtn.classList.add('hidden');
    $('btnScan').disabled = false;
    if (st.state === 'error') return toast('扫描出错: ' + st.error, true);
    p.querySelector('.label').textContent = `完成 · ${st.asset_count} 资源`;
    renderTypeList(st.type_counts);
    loadAssets();
    loadFavorites();
    $('btnExportAll').disabled = false;
  }, 500);
}
$('btnScanPause').onclick = async () => {
  const btn = $('btnScanPause');
  btn.disabled = true;
  try {
    const action = btn.textContent === '暂停' ? 'pause' : 'resume';
    await postJson('/api/scan/' + action, {});
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
};

$('btnRemoveProject').onclick = async () => {
  if (!confirm('移除当前项目？\n将停止扫描/导出、清空资源清单并释放内存（收藏保留）。')) return;
  try { await postJson('/api/project/remove', {}); }
  catch (e) { return toast(e.message, true); }
  resetProjectUI();
  toast('项目已移除，内存已释放');
};
function resetProjectUI() {
  clearTimeout(state.scanTimer);
  clearTimeout(state.exportTimer);
  Object.assign(state, {
    detected: null, typeFilter: null, search: '', total: 0, items: [], page: 0,
    current: null, favMode: false, favType: null, sortKey: null, sortDir: 1,
  });
  state.selected.clear();
  selRow = null;
  $('pathInput').value = '';
  $('projRow').classList.add('hidden');
  $('engineBadge').classList.add('hidden');
  $('scanProgress').classList.add('hidden');
  $('btnScanPause').classList.add('hidden');
  $('btnScan').disabled = true;
  $('btnExportAll').disabled = true;
  $('btnGallery').disabled = true;
  closeGallery();
  $('searchInput').value = '';
  $('checkAll').checked = false;
  for (const id of ['thName', 'thSize']) $(id).querySelector('.arrow').textContent = '';
  updateExportButtons();
  $('typeList').innerHTML = '<li class="empty">尚未扫描</li>';
  $('assetTbody').innerHTML = '<tr><td colspan="7" class="empty">选择游戏目录 → 识别引擎 → 扫描资源</td></tr>';
  $('listCount').textContent = '0 项';
  $('pageLabel').textContent = '1 / 1';
  $('btnPrev').disabled = $('btnNext').disabled = true;
  loadFavorites();
  hideAnimPanel();
  Object.assign(anim, { charId: null, clipName: '', clips: [], duration: 0, textured: false, seeking: false });
  previewToken++;
  destroyViewerCanvas();
  $('previewTitle').textContent = '预览';
  $('previewMeta').innerHTML = '';
  $('previewActions').innerHTML = '';
  $('previewBody').innerHTML = '<div class="placeholder">点击左侧资源进行预览</div>';
  $('exportDialog').classList.add('hidden');
  closeFavDialog();
}

function renderTypeList(counts) {
  const ul = $('typeList');
  ul.innerHTML = '';
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const entries = [['全部', '', total, null],
    ...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([t, c]) => [zh(t), t, c, t])];
  for (const [label, en, cnt, type] of entries) {
    const li = document.createElement('li');
    li.innerHTML = `<span>${label}${en ? `<span class="en">${en}</span>` : ''}</span><span class="cnt">${cnt}</span>`;
    if (type === state.typeFilter) li.classList.add('active');
    li.onclick = () => {
      state.typeFilter = type; state.page = 0;
      state.favMode = false; state.favType = null;
      [...ul.children].forEach(x => x.classList.remove('active'));
      li.classList.add('active');
      [...$('favList').querySelectorAll('.fav-group')].forEach(x => x.classList.remove('active'));
      loadAssets();
    };
    ul.appendChild(li);
  }
}

// ---------------- 资源列表（全量加载元数据，排序后分页渲染） ----------------
async function loadAssets() {
  if (state.favMode) { applyFavList(); return; }
  const params = new URLSearchParams({ offset: 0, limit: 1000000, search: state.search });
  if (state.typeFilter) params.set('type', state.typeFilter);
  const d = await (await api('/api/assets?' + params)).json();
  state.items = d.items; state.total = d.total;
  renderTable();
}
function applyFavList() {
  const q = state.search.toLowerCase();
  let arr = state.favorites;
  if (state.favType) arr = arr.filter(a => a.type === state.favType);
  if (q) arr = arr.filter(a => (a.alias || '').toLowerCase().includes(q) || a.name.toLowerCase().includes(q));
  state.items = arr; state.total = arr.length;
  renderTable();
}
function extraText(a) {
  const e = a.extra || {};
  if (e.parts) return `${e.parts} 部件 · ${e.bones} 骨骼`;
  if (e.width) return `${e.width}×${e.height}`;
  if (e.vertices) return `${e.vertices} 顶点`;
  if (e.length) return `${Number(e.length).toFixed(1)}s`;
  if (e.shader) return e.shader;
  if (e.sample_rate) return `${e.sample_rate} fps`;
  return '';
}
let selRow = null;
function sortedItems() {
  if (!state.sortKey) return state.items;
  const dir = state.sortDir;
  const arr = [...state.items];
  if (state.sortKey === 'name')
    arr.sort((a, b) => dir * a.name.localeCompare(b.name, 'zh-CN', { numeric: true, sensitivity: 'base' }));
  else
    arr.sort((a, b) => dir * (a.size - b.size));
  return arr;
}
function pageItems() {
  return sortedItems().slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
}
function bindSort(thId, key) {
  $(thId).onclick = () => {
    if (state.sortKey === key) state.sortDir = -state.sortDir;
    else { state.sortKey = key; state.sortDir = 1; }
    for (const [id, k] of [['thName', 'name'], ['thSize', 'size']]) {
      $(id).querySelector('.arrow').textContent =
        state.sortKey === k ? (state.sortDir === 1 ? ' ▲' : ' ▼') : '';
    }
    state.page = 0;
    $('assetTableWrap').scrollTop = 0;
    renderTable();
  };
}
bindSort('thName', 'name');
bindSort('thSize', 'size');
function buildRow(a) {
  const tr = document.createElement('tr');
  tr.dataset.id = a.id;
  if (state.current?.id === a.id) { tr.classList.add('selected'); selRow = tr; }
  const fav = state.favIds.has(a.id);
  const thumb = IMAGE_TYPES.includes(a.type)
    ? `<img class="thumb" loading="lazy" decoding="async" alt="" src="/api/thumbnail/${a.id}" onerror="this.remove()">`
    : '';
  const nameInner = a.alias
    ? `<div class="fav-name-cell"><span>${esc(a.alias)}</span><span class="orig">${esc(a.name)}</span></div>`
    : `<span class="name-text">${esc(a.name)}</span>`;
  const nameCell = `<td class="name" title="${esc(a.name)}"><div class="name-wrap">${nameInner}${thumb}</div></td>`;
  tr.innerHTML = `
    <td class="col-check"><input type="checkbox" ${state.selected.has(a.id) ? 'checked' : ''}></td>
    <td class="col-fav"><button class="fav-star ${fav ? 'on' : ''}" title="${fav ? '取消收藏' : '收藏'}">${fav ? '★' : '☆'}</button></td>
    ${nameCell}
    <td><span class="type-tag type-${a.type}" title="${a.type}">${zh(a.type)}</span></td>
    <td class="dim" title="${esc(a.file)}${a.container ? ' → ' + esc(a.container) : ''}">${esc(a.file)}</td>
    <td class="dim">${fmtSize(a.size)}</td>
    <td class="dim">${extraText(a)}</td>`;
  const cb = tr.querySelector('input');
  cb.onclick = (ev) => {
    ev.stopPropagation();
    cb.checked ? state.selected.add(a.id) : state.selected.delete(a.id);
    updateExportButtons();
  };
  tr.querySelector('.fav-star').onclick = (ev) => { ev.stopPropagation(); toggleFavorite(a); };
  tr.onclick = () => selectAsset(a);
  return tr;
}
function selectAsset(a) {
  state.current = a;
  selRow?.classList.remove('selected');
  const tr = document.querySelector(`#assetTbody tr[data-id="${a.id}"]`);
  if (tr) {
    tr.classList.add('selected');
    selRow = tr;
    tr.scrollIntoView({ block: 'nearest' });
  }
  showPreview(a);
}
function navigateList(dir) {
  const items = pageItems();
  if (!items.length) return;
  const idx = items.findIndex(x => x.id === state.current?.id);
  const ni = idx === -1 ? (dir > 0 ? 0 : items.length - 1) : idx + dir;
  if (ni < 0) {
    if (state.page === 0) return;
    state.page--; renderTable();
    const prev = pageItems();
    selectAsset(prev[prev.length - 1]);
  } else if (ni >= items.length) {
    if (state.page >= Math.ceil(state.total / PAGE_SIZE) - 1) return;
    state.page++; renderTable();
    selectAsset(pageItems()[0]);
  } else {
    selectAsset(items[ni]);
  }
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  const t = e.target;
  if (t && (t.isContentEditable || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' ||
      (t.tagName === 'INPUT' && t.type !== 'checkbox'))) return;
  if (!$('folderDialog').classList.contains('hidden') || !$('exportDialog').classList.contains('hidden') ||
      !$('favDialog').classList.contains('hidden') || !$('galleryOverlay').classList.contains('hidden')) return;
  e.preventDefault();
  navigateList(e.key === 'ArrowDown' ? 1 : -1);
});
function renderTable() {
  const tb = $('assetTbody');
  const wrap = $('assetTableWrap');
  const scroll = wrap.scrollTop;
  selRow = null;
  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  const items = pageItems();
  tb.innerHTML = '';
  $('listCount').textContent = `${state.total} 项`;
  $('pageLabel').textContent = `${state.page + 1} / ${pages}`;
  $('btnPrev').disabled = state.page === 0;
  $('btnNext').disabled = state.page >= pages - 1;
  if (!items.length) {
    tb.innerHTML = '<tr><td colspan="7" class="empty">无匹配资源</td></tr>';
    $('checkAll').checked = false;
    return;
  }
  const frag = document.createDocumentFragment();
  for (const a of items) frag.appendChild(buildRow(a));
  tb.appendChild(frag);
  wrap.scrollTop = scroll;
  $('checkAll').checked = items.every(a => state.selected.has(a.id));
}
$('btnPrev').onclick = () => { state.page--; $('assetTableWrap').scrollTop = 0; renderTable(); };
$('btnNext').onclick = () => { state.page++; $('assetTableWrap').scrollTop = 0; renderTable(); };
$('checkAll').onclick = (ev) => {
  for (const a of pageItems()) ev.target.checked ? state.selected.add(a.id) : state.selected.delete(a.id);
  renderTable(); updateExportButtons();
};
let searchTimer;
$('searchInput').oninput = (ev) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.search = ev.target.value.trim(); state.page = 0; loadAssets(); }, 300);
};
function updateExportButtons() {
  $('btnExportSelected').textContent = `导出选中 (${state.selected.size})`;
  $('btnExportSelected').disabled = state.selected.size === 0;
}

// ---------------- 我的收藏 ----------------
function initSidebarToggles() {
  const apply = (title, list, open) => {
    list.classList.toggle('hidden', !open);
    title.classList.toggle('collapsed', !open);
  };
  const typeOpen = localStorage.getItem('ae_typeOpen') !== '0';  // 资源类型默认展开
  const favOpen = localStorage.getItem('ae_favOpen') === '1';    // 我的收藏默认收起
  apply($('typeTitle'), $('typeList'), typeOpen);
  apply($('favTitle'), $('favList'), favOpen);
  $('typeTitle').onclick = () => {
    const open = $('typeList').classList.contains('hidden');
    apply($('typeTitle'), $('typeList'), open);
    localStorage.setItem('ae_typeOpen', open ? '1' : '0');
  };
  $('favTitle').onclick = () => {
    const open = $('favList').classList.contains('hidden');
    apply($('favTitle'), $('favList'), open);
    localStorage.setItem('ae_favOpen', open ? '1' : '0');
  };
  try { state.favGroupOpen = JSON.parse(localStorage.getItem('ae_favGroups') || '{}'); }
  catch { state.favGroupOpen = {}; }
}
function favGroupOpen(type) { return state.favGroupOpen[type] !== false; }
function setFavGroupOpen(type, open) {
  state.favGroupOpen[type] = open;
  localStorage.setItem('ae_favGroups', JSON.stringify(state.favGroupOpen));
}
async function loadFavorites() {
  try {
    const d = await (await api('/api/favorites')).json();
    state.favorites = d.items || [];
    state.favIds = new Set(state.favorites.map(f => f.id));
  } catch { state.favorites = []; state.favIds = new Set(); }
  renderFavMenu();
  if (state.favMode) applyFavList();
}
function renderFavMenu() {
  const ul = $('favList');
  $('favCount').textContent = state.favorites.length ? ` (${state.favorites.length})` : '';
  ul.innerHTML = '';
  if (!state.favorites.length) { ul.innerHTML = '<li class="empty">暂无收藏</li>'; return; }
  const groups = {};
  for (const f of state.favorites) (groups[f.type] ||= []).push(f);
  const order = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length);
  for (const type of order) {
    const items = groups[type];
    const open = favGroupOpen(type);
    const g = document.createElement('li');
    g.className = 'fav-group' + (state.favMode && state.favType === type ? ' active' : '');
    g.innerHTML = `<span class="chev ${open ? '' : 'closed'}">▾</span><span class="glabel">${zh(type)}</span><span class="cnt">${items.length}</span>`;
    g.title = '单击展开/收起，双击在列表中查看';
    const sub = document.createElement('ul');
    sub.className = 'fav-items' + (open ? '' : ' hidden');
    g.onclick = () => {
      const o = !favGroupOpen(type);
      setFavGroupOpen(type, o);
      sub.classList.toggle('hidden', !o);
      g.querySelector('.chev').classList.toggle('closed', !o);
    };
    g.ondblclick = () => enterFavMode(type);
    ul.appendChild(g);
    for (const f of items) {
      const li = document.createElement('li');
      li.className = 'fav-item';
      li.innerHTML = `<span class="star">★</span><span class="alias" title="原名：${esc(f.name)}（双击重命名）">${esc(f.alias)}</span><span class="rn" title="重命名">✎</span><span class="rm" title="取消收藏">✕</span>`;
      li.onclick = () => selectAsset(f);
      li.ondblclick = () => openRenameDialog(f);
      li.querySelector('.rn').onclick = (ev) => { ev.stopPropagation(); openRenameDialog(f); };
      li.querySelector('.rm').onclick = (ev) => { ev.stopPropagation(); removeFavorite(f.fav_key); };
      sub.appendChild(li);
    }
    ul.appendChild(sub);
  }
}
function enterFavMode(type) {
  state.favMode = true; state.favType = type; state.page = 0; state.typeFilter = null;
  [...$('typeList').children].forEach(x => x.classList.remove('active'));
  renderFavMenu();
  loadAssets();
}
function favKeyOf(a) {
  const f = state.favorites.find(x => x.id === a.id);
  return f ? f.fav_key : null;
}
function toggleFavorite(a) {
  if (state.favIds.has(a.id)) { const k = favKeyOf(a); if (k) removeFavorite(k); }
  else openFavDialog(a);
}
function openFavDialog(a) {
  state.favPending = a; state.favRenameKey = null;
  $('favDialogTitle').textContent = '收藏资源';
  $('favConfirm').textContent = '收藏';
  $('favOrigName').textContent = a.name;
  $('favAlias').value = a.alias || a.name;
  $('favDialog').classList.remove('hidden');
  setTimeout(() => { $('favAlias').focus(); $('favAlias').select(); }, 30);
}
function openRenameDialog(f) {
  state.favPending = null; state.favRenameKey = f.fav_key;
  $('favDialogTitle').textContent = '重命名收藏';
  $('favConfirm').textContent = '保存';
  $('favOrigName').textContent = f.name;
  $('favAlias').value = f.alias;
  $('favDialog').classList.remove('hidden');
  setTimeout(() => { $('favAlias').focus(); $('favAlias').select(); }, 30);
}
function closeFavDialog() {
  $('favDialog').classList.add('hidden');
  state.favPending = null; state.favRenameKey = null;
}
async function confirmFavorite() {
  const alias = $('favAlias').value.trim();
  if (!alias) return toast('请填写收藏名称', true);
  try {
    if (state.favRenameKey) {
      await postJson('/api/favorites/rename', { key: state.favRenameKey, alias });
      closeFavDialog();
      await loadFavorites();
      renderTable();
      toast('已重命名：' + alias);
    } else {
      const a = state.favPending;
      if (!a) return;
      await postJson('/api/favorites', { aid: a.id, alias });
      closeFavDialog();
      await loadFavorites();
      renderTable();
      toast('已收藏：' + alias);
    }
  } catch (e) { toast(e.message, true); }
}
async function removeFavorite(key) {
  try {
    await postJson('/api/favorites/remove', { key });
    await loadFavorites();
    renderTable();
    toast('已取消收藏');
  } catch (e) { toast(e.message, true); }
}
$('favConfirm').onclick = confirmFavorite;
$('favCancel').onclick = closeFavDialog;
$('favAlias').addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmFavorite();
  if (e.key === 'Escape') closeFavDialog();
});
$('favDialog').addEventListener('click', e => { if (e.target === $('favDialog')) closeFavDialog(); });

// ---------------- 3D 预览 ----------------
let viewer = null;
const anim = {
  active: false, charId: null, clipId: null, clipName: '', textured: false,
  action: null, duration: 0, clips: [], scanTimer: null, seeking: false,
};
function initViewer() {
  if (viewer) return viewer;
  const body = $('previewBody');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x191c22);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.001, 5000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x556677, 1.4));
  const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(3, 5, 4); scene.add(key);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.7); fill.position.set(-4, 2, -3); scene.add(fill);
  const grid = new THREE.GridHelper(10, 20, 0x334455, 0x2a2e37); scene.add(grid);
  const root = new THREE.Group(); scene.add(root);
  const v = { renderer, scene, camera, controls, root, grid, mixer: null, boneViz: null, wireframe: false, current: null };
  // 按包围球 + 水平/垂直视场角中较小者计算取景距离，窄画布也不会裁切模型
  v.fitDistance = () => {
    const fovV = THREE.MathUtils.degToRad(camera.fov);
    const fovH = 2 * Math.atan(Math.tan(fovV / 2) * camera.aspect);
    return (v._radius || 1) / Math.sin(Math.min(fovV, fovH) / 2) * 1.05;
  };
  v.resize = () => {
    const w = body.clientWidth, h = body.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (v.current && v._radius) {
      // 画布比例变化后沿当前视线方向重新取景，保持角度、保证完整可见
      const dir = camera.position.clone().sub(controls.target);
      if (dir.lengthSq() > 1e-12)
        camera.position.copy(controls.target).add(dir.normalize().multiplyScalar(v.fitDistance()));
    }
  };
  new ResizeObserver(v.resize).observe(body);
  v.clear = () => {
    v.mixer = null; v.boneViz = null; v.current = null;
    while (v.root.children.length) {
      const c = v.root.children[0];
      v.root.remove(c);
      c.traverse?.(o => {
        o.geometry?.dispose?.();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => {
          for (const k of Object.keys(m)) { const t = m[k]; if (t && t.isTexture) t.dispose(); }
          m.dispose();
        });
      });
    }
  };
  v.frame = (obj) => {
    v.current = obj;
    const box = new THREE.Box3().setFromObject(obj);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    obj.position.sub(center);
    v._radius = size.length() / 2 || 1;
    v.grid.scale.setScalar(maxDim / 10);
    v.grid.position.y = -size.y / 2;
    const dist = v.fitDistance();
    camera.position.set(dist * 0.55, dist * 0.4, dist * 0.73);
    camera.near = maxDim / 1000; camera.far = maxDim * 100 + dist * 2;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0); controls.update();
  };
  const clock = new THREE.Clock();
  renderer.setAnimationLoop(() => {
    const dt = clock.getDelta();
    if (v.mixer) v.mixer.update(dt);
    if (v.boneViz) v.boneViz.update();
    animFrameUpdate();
    controls.update();
    renderer.render(scene, camera);
  });
  viewer = v;
  return v;
}
const gltfLoader = new GLTFLoader();
const parseGLB = (buf) => new Promise((res, rej) => gltfLoader.parse(buf, '', res, rej));

function mountCanvas() {
  const v = initViewer();
  $('previewBody').innerHTML = '';
  $('previewBody').appendChild(v.renderer.domElement);
  v.resize();
  return v;
}
function showPreviewLoading(text = '加载中…') {
  hidePreviewLoading();
  const el = document.createElement('div');
  el.className = 'spinner'; el.id = 'previewLoading'; el.textContent = text;
  $('previewBody').appendChild(el);
}
function hidePreviewLoading() {
  document.getElementById('previewLoading')?.remove();
}
async function showMeshPreview(a, token, opts = {}) {
  const { refresh = false, textured = false } = opts;
  hideAnimPanel();
  const v = mountCanvas();
  showPreviewLoading(textured ? '贴图加载中…' : '模型加载中…');
  const q = [];
  if (refresh) q.push('refresh=1');
  if (textured) q.push('textured=1');
  const url = `/api/preview/${a.id}` + (q.length ? '?' + q.join('&') : '');
  let gltf;
  try {
    const buf = await (await api(url)).arrayBuffer();
    gltf = await parseGLB(buf);
    if (token !== previewToken) return;
    v.clear();
    v.root.add(gltf.scene);
    v.frame(gltf.scene);
  } finally {
    hidePreviewLoading();
  }
  const actions = [
    { label: '线框', fn: (btn) => {
      v.wireframe = !v.wireframe;
      gltf.scene.traverse(o => { if (o.isMesh) o.material.wireframe = v.wireframe; });
      btn.classList.toggle('primary', v.wireframe);
    }},
    { label: '重置视角', fn: () => v.current && v.frame(v.current) },
  ];
  if (a.type === 'Character') {
    actions.unshift({ label: '角色组装', fn: async (btn) => {
      btn.disabled = true; btn.textContent = '组装中…';
      try {
        await showMeshPreview(a, ++previewToken, { refresh: true, textured });
      } catch (e) {
        btn.disabled = false; btn.textContent = '角色组装';
        alert('组装失败：' + e.message);
      }
    }});
    actions.unshift({ label: '动画播放', fn: () => openAnimPanel(a, textured) });
  }
  if (a.type === 'Mesh' || a.type === 'Character') {
    actions.unshift({ label: textured ? '素模' : '材质贴图', fn: async (btn) => {
      btn.disabled = true; const old = btn.textContent; btn.textContent = '加载中…';
      try {
        await showMeshPreview(a, ++previewToken, { textured: !textured });
      } catch (e) {
        btn.disabled = false; btn.textContent = old;
        alert('贴图加载失败：' + e.message);
      }
    }});
  }
  setPreviewActions(a, actions);
}
function buildBoneViz(v, scene) {
  const nodes = [];
  scene.traverse(o => { if (o.isBone || o.type === 'Object3D' || o.isGroup) nodes.push(o); });
  const pairs = [];
  const mat = new THREE.MeshBasicMaterial({ color: 0xe8b34b });
  const geo = new THREE.SphereGeometry(0.012, 8, 8);
  for (const n of nodes) {
    n.add(new THREE.Mesh(geo, mat));
    if (n.parent && nodes.includes(n.parent)) pairs.push([n.parent, n]);
  }
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3));
  const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ color: 0x4f8cff }));
  lines.frustumCulled = false;
  const wp = new THREE.Vector3();
  return {
    lines,
    update() {
      const arr = lineGeo.attributes.position.array;
      pairs.forEach(([p, c], i) => {
        p.getWorldPosition(wp); arr.set([wp.x, wp.y, wp.z], i * 6);
        c.getWorldPosition(wp); arr.set([wp.x, wp.y, wp.z], i * 6 + 3);
      });
      lineGeo.attributes.position.needsUpdate = true;
    },
    dispose() { lines.geometry.dispose(); lines.material.dispose(); geo.dispose(); mat.dispose(); },
  };
}
async function showAnimPreview(a, token) {
  const r = await api(`/api/preview/${a.id}`);
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('gltf-binary')) {
    const v = mountCanvas();
    showPreviewLoading('动画加载中…');
    let gltf;
    try {
      gltf = await parseGLB(await r.arrayBuffer());
      if (token !== previewToken) return;
      v.clear();
      v.root.add(gltf.scene);
      const viz = buildBoneViz(v, gltf.scene);
      v.root.add(viz.lines);
      v.boneViz = viz;
      v.frame(gltf.scene);
    } finally {
      hidePreviewLoading();
    }
    if (gltf.animations.length) {
      v.mixer = new THREE.AnimationMixer(gltf.scene);
      const action = v.mixer.clipAction(gltf.animations[0]);
      action.play();
      setPreviewActions(a, [
        { label: '暂停/播放', fn: () => { action.paused = !action.paused; } },
        { label: '重置视角', fn: () => v.current && v.frame(v.current) },
      ]);
    }
  } else {
    const d = await r.json();
    if (token !== previewToken) return;
    showHumanoidInfo(a, d.info || d);
  }
}
function showHumanoidInfo(a, info) {
  destroyViewerCanvas();
  const dur = info.stop_time ?? '?';
  $('previewBody').innerHTML = `
    <div class="anim-note">
      <h4>Humanoid 肌肉动画</h4>
      <p>名称：<b>${info.name}</b></p>
      <p>时长：${Number(dur).toFixed(2)}s · 采样率：${info.sample_rate} fps</p>
      <p>肌肉曲线：${info.muscle_curves ?? '?'} · 绑定：${info.bindings ?? '?'} · 事件：${info.events ?? 0}</p>
      <p style="margin-top:10px">该动画以 Humanoid 肌肉空间存储，导出为无损 JSON（含绑定与全部曲线数据）。骨骼重定向预览需 Avatar 数据，列入后续版本。</p>
    </div>`;
  setPreviewActions(a);
}
function destroyViewerCanvas() { if (viewer) { viewer.clear(); viewer.boneViz?.dispose?.(); } }
function setPreviewActions(a, extra = []) {
  const box = $('previewActions');
  box.innerHTML = '';
  for (const { label, fn } of extra) {
    const b = document.createElement('button');
    b.className = 'btn small'; b.textContent = label;
    b.onclick = () => fn(b);
    box.appendChild(b);
  }
  const exp = document.createElement('button');
  exp.className = 'btn small primary'; exp.textContent = '导出此资源';
  exp.onclick = () => openExportDialog([a.id]);
  box.appendChild(exp);
}

// ---------------- 角色动画播放 ----------------
function hideAnimPanel() {
  anim.active = false; anim.action = null; anim.clipId = null;
  clearTimeout(anim.scanTimer);
  $('animPanel').classList.add('hidden');
  $('animDropdown').classList.add('hidden');
}
function openAnimPanel(a, textured) {
  anim.active = true; anim.charId = a.id; anim.textured = textured;
  anim.clipId = null; anim.clipName = ''; anim.action = null; anim.clips = [];
  $('animPanel').classList.remove('hidden');
  $('animClipBtn').textContent = '选择动画 ▾';
  $('animPlay').disabled = true; $('animSeek').disabled = true;
  $('animExportFrame').disabled = true;
  $('animTime').textContent = '--';
  $('animSearch').value = '';
  $('animList').innerHTML = '';
  $('animDropdown').classList.remove('hidden');
  pollClips();
}
function pollClips() {
  clearTimeout(anim.scanTimer);
  anim.scanTimer = setTimeout(async () => {
    if (!anim.active) return;
    let st;
    try { st = await (await api(`/api/character_clips/${anim.charId}`)).json(); }
    catch (e) { $('animScanState').textContent = '加载失败：' + e.message; return; }
    if (!anim.active) return;
    if (st.state === 'running') {
      $('animScanState').textContent = `正在搜索兼容动画… ${st.done}/${st.total}`;
      return pollClips();
    }
    if (st.state === 'error') { $('animScanState').textContent = '搜索出错：' + st.error; return; }
    anim.clips = st.results || [];
    $('animScanState').textContent = `共 ${anim.clips.length} 个兼容动画（★ 为本角色推荐）`;
    renderAnimList();
  }, 400);
}
function renderAnimList() {
  const box = $('animList');
  const q = $('animSearch').value.trim().toLowerCase();
  const items = anim.clips.filter(c => !q || c.name.toLowerCase().includes(q)).slice(0, 300);
  box.innerHTML = '';
  if (!items.length) { box.innerHTML = '<div class="empty">无匹配动画</div>'; return; }
  const frag = document.createDocumentFragment();
  for (const c of items) {
    const div = document.createElement('div');
    div.className = 'item' + (c.id === anim.clipId ? ' active' : '');
    div.innerHTML = `<span class="nm">${c.boost ? '<span class="boost">★</span> ' : ''}${c.name}</span>` +
      `<span class="meta">${c.duration ? c.duration.toFixed(2) + 's · ' : ''}${c.file}</span>`;
    div.onclick = () => loadClip(c.id, c.name);
    frag.appendChild(div);
  }
  box.appendChild(frag);
}
async function loadClip(id, name) {
  const token = ++previewToken;
  $('animDropdown').classList.add('hidden');
  $('animClipBtn').textContent = '加载中…';
  const v = viewer || initViewer();
  try {
    const url = `/api/character_anim/${anim.charId}?clip=${id}&textured=${anim.textured ? 1 : 0}`;
    const gltf = await parseGLB(await (await api(url)).arrayBuffer());
    if (token !== previewToken || !anim.active) return;
    v.clear();
    v.root.add(gltf.scene);
    v.frame(gltf.scene);
    anim.clipId = id; anim.clipName = name;
    $('animClipBtn').textContent = name + ' ▾';
    if (gltf.animations.length) {
      v.mixer = new THREE.AnimationMixer(gltf.scene);
      const action = v.mixer.clipAction(gltf.animations[0]);
      action.setLoop(THREE.LoopRepeat, Infinity);
      action.play();
      anim.action = action;
      anim.duration = gltf.animations[0].duration || 0;
      $('animSeek').max = Math.max(1, Math.round(anim.duration * 1000));
      $('animSeek').value = 0;
      $('animPlay').disabled = false; $('animSeek').disabled = false;
      $('animExportFrame').disabled = false;
    } else {
      $('animClipBtn').textContent = name + '（无动画）';
    }
    renderAnimList();
  } catch (e) {
    if (token !== previewToken) return;
    $('animClipBtn').textContent = '选择动画 ▾';
    toast('动画加载失败：' + e.message, true);
  }
}
function animFrameUpdate() {
  if (!anim.active || !anim.action || anim.seeking) return;
  const t = anim.action.time;
  $('animSeek').value = Math.round(t * 1000);
  $('animTime').textContent = `${t.toFixed(2)} / ${anim.duration.toFixed(2)}s`;
  $('animPlay').textContent = anim.action.paused ? '播放' : '暂停';
}
async function exportCurrentFrame() {
  if (!anim.action) return;
  const t = anim.action.time;
  const btn = $('animExportFrame');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '导出中…';
  try {
    const url = `/api/character_anim/${anim.charId}?clip=${anim.clipId}&pose=${t.toFixed(4)}` +
      `&textured=${anim.textured ? 1 : 0}`;
    const buf = await (await api(url)).arrayBuffer();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([buf], { type: 'model/gltf-binary' }));
    a.download = `${state.current?.name || 'character'}_${anim.clipName}_t${t.toFixed(2)}.glb`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast('已导出当前帧姿态');
  } catch (e) { toast('导出失败：' + e.message, true); }
  btn.disabled = false; btn.textContent = old;
}
function closeAnim() {
  const a = state.current;
  const textured = anim.textured;
  hideAnimPanel();
  if (a && a.type === 'Character') showMeshPreview(a, ++previewToken, { textured });
}
$('animClipBtn').onclick = () => {
  const dd = $('animDropdown');
  dd.classList.toggle('hidden');
  if (!dd.classList.contains('hidden')) { renderAnimList(); $('animSearch').focus(); }
};
$('animSearch').oninput = renderAnimList;
$('animPlay').onclick = () => { if (anim.action) anim.action.paused = !anim.action.paused; };
$('animSeek').oninput = (ev) => {
  if (!anim.action || !viewer?.mixer) return;
  anim.seeking = true;
  const t = ev.target.value / 1000;
  anim.action.time = t;
  viewer.mixer.update(0);
  $('animTime').textContent = `${t.toFixed(2)} / ${anim.duration.toFixed(2)}s`;
};
$('animSeek').onchange = () => { anim.seeking = false; };
$('animExportFrame').onclick = exportCurrentFrame;
$('animClose').onclick = closeAnim;

// ---------------- 预览分发 ----------------
let previewToken = 0;
async function showPreview(a) {
  const token = ++previewToken;
  $('previewTitle').textContent = `${a.name}（${zh(a.type)}）`;
  $('previewMeta').innerHTML =
    `<b>${zh(a.type)} ${a.type}</b> · ${fmtSize(a.size)} · 来源 ${a.file}` +
    (a.container ? `<br>容器：${a.container}` : '') +
    `<br>PathID：${a.path_id}` + (extraText(a) ? ` · ${extraText(a)}` : '');
  setPreviewActions(a);
  $('previewBody').innerHTML = '<div class="spinner">加载中…</div>';
  try {
    if (a.type === 'Mesh' || a.type === 'Character') return await showMeshPreview(a, token);
    if (a.type === 'AnimationClip') return await showAnimPreview(a, token);
    destroyViewerCanvas();
    if (['Texture2D', 'Sprite', 'Cubemap'].includes(a.type)) {
      const blob = await (await api(`/api/preview/${a.id}`)).blob();
      if (token !== previewToken) return;
      const img = document.createElement('img');
      img.className = 'tex';
      img.src = URL.createObjectURL(blob);
      $('previewBody').innerHTML = '';
      $('previewBody').appendChild(img);
      return;
    }
    if (a.type === 'AudioClip') {
      const blob = await (await api(`/api/preview/${a.id}`)).blob();
      if (token !== previewToken) return;
      const au = document.createElement('audio');
      au.controls = true; au.src = URL.createObjectURL(blob);
      $('previewBody').innerHTML = '';
      $('previewBody').appendChild(au);
      return;
    }
    const text = await (await api(`/api/preview/${a.id}`)).text();
    if (token !== previewToken) return;
    const pre = document.createElement('pre');
    pre.textContent = text.slice(0, 200000);
    $('previewBody').innerHTML = '';
    $('previewBody').appendChild(pre);
  } catch (e) {
    if (token !== previewToken) return;
    $('previewBody').innerHTML = `<div class="placeholder">预览失败：${e.message}</div>`;
  }
}

// ---------------- 导出 ----------------
function openExportDialog(ids) {
  state.exportIds = ids;
  $('exportDialog').classList.remove('hidden');
  $('exportProgress').classList.add('hidden');
  $('exportErrors').textContent = '';
  $('btnExportStart').disabled = false;
  $('btnExportStart').textContent = `开始导出 (${ids.length} 项)`;
}
$('btnExportSelected').onclick = () => openExportDialog([...state.selected]);
$('btnExportAll').onclick = async () => {
  const params = new URLSearchParams({ offset: 0, limit: 1000000, search: state.search });
  if (state.typeFilter) params.set('type', state.typeFilter);
  const d = await (await api('/api/assets?' + params)).json();
  openExportDialog(d.items.map(a => a.id));
};
$('btnExportClose').onclick = () => { $('exportDialog').classList.add('hidden'); clearTimeout(state.exportTimer); };
$('btnOpenExports').onclick = async () => {
  const st = await (await api('/api/export/status')).json();
  try { await postJson('/api/open_folder', { path: st.out_dir || 'exports' }); }
  catch { toast('导出目录尚未创建，请先执行一次导出', true); }
};
$('btnExportStart').onclick = async () => {
  const formats = [];
  if ($('fmtGlb').checked) formats.push('glb');
  if ($('fmtObj').checked) formats.push('obj');
  try {
    const d = await (await postJson('/api/export', {
      ids: state.exportIds, out_dir: $('exportDir').value.trim() || null, mesh_formats: formats,
    })).json();
    $('btnExportStart').disabled = true;
    $('exportProgress').classList.remove('hidden');
    toast('导出开始 → ' + d.out_dir);
    pollExport();
  } catch (e) { toast(e.message, true); }
};
function pollExport() {
  clearTimeout(state.exportTimer);
  state.exportTimer = setTimeout(async () => {
    const st = await (await api('/api/export/status')).json();
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    const p = $('exportProgress');
    p.querySelector('.bar').style.width = pct + '%';
    p.querySelector('.label').textContent = `${st.done}/${st.total} · ${st.current || ''}`;
    if (st.state === 'running') return pollExport();
    $('btnExportStart').disabled = false;
    if (st.errors?.length) $('exportErrors').innerHTML = st.errors.map(e => `<div>${e}</div>`).join('');
    toast(`导出完成：${st.written_count} 个文件 → ${st.out_dir}`, st.state === 'error');
  }, 500);
}

$('pathInput').addEventListener('keydown', e => { if (e.key === 'Enter') doDetect(); });

// ---------------- CG 画廊 ----------------
const gallery = {
  open: false, items: [], total: 0, page: 0, pageSize: 60,
  search: '', bigOnly: true, cgOnly: true, cgAvailable: false,
  lbIndex: -1, zoom: 1, panX: 0, panY: 0,
};
function openGallery() {
  gallery.open = true;
  $('galleryOverlay').classList.remove('hidden');
  loadGallery();
}
function closeGallery() {
  gallery.open = false;
  closeLightbox();
  $('galleryOverlay').classList.add('hidden');
}
async function loadGallery() {
  const params = new URLSearchParams({
    min_side: gallery.bigOnly ? 400 : 0, search: gallery.search,
    cg_only: gallery.cgOnly, offset: gallery.page * gallery.pageSize, limit: gallery.pageSize,
  });
  const d = await (await api('/api/gallery?' + params)).json();
  gallery.cgAvailable = d.cg_available;
  // 无 CG 命名来源的游戏：自动回退到“仅大图”，避免空画廊
  if (d.total === 0 && gallery.cgOnly && !d.cg_available) {
    gallery.cgOnly = false; $('galleryCgOnly').checked = false;
    return loadGallery();
  }
  gallery.items = d.items; gallery.total = d.total;
  renderGalleryGrid();
}
function renderGalleryGrid() {
  const grid = $('galleryGrid');
  const pages = Math.max(1, Math.ceil(gallery.total / gallery.pageSize));
  gallery.page = Math.min(gallery.page, pages - 1);
  $('galleryCount').textContent = `${gallery.total} 张`;
  $('galleryPage').textContent = `${gallery.page + 1} / ${pages}`;
  $('galleryPrev').disabled = gallery.page === 0;
  $('galleryNext').disabled = gallery.page >= pages - 1;
  grid.innerHTML = '';
  if (!gallery.items.length) { grid.innerHTML = '<div class="gallery-empty">无匹配图片</div>'; return; }
  const frag = document.createDocumentFragment();
  gallery.items.forEach((a, i) => {
    const card = document.createElement('div');
    card.className = 'g-card';
    card.innerHTML = `<div class="g-img"><img loading="lazy" decoding="async" alt="" src="/api/thumbnail/${a.id}" onerror="this.parentNode.classList.add('broken')"></div><div class="g-cap" title="${esc(a.name)}">${esc(a.name)}</div>`;
    card.onclick = () => openLightbox(i);
    frag.appendChild(card);
  });
  grid.appendChild(frag);
  grid.scrollTop = 0;
}
$('btnGallery').onclick = openGallery;
$('galleryClose').onclick = closeGallery;
$('galleryPrev').onclick = () => { gallery.page--; loadGallery(); };
$('galleryNext').onclick = () => { gallery.page++; loadGallery(); };
$('galleryBigOnly').onchange = (e) => { gallery.bigOnly = e.target.checked; gallery.page = 0; loadGallery(); };
$('galleryCgOnly').onchange = (e) => { gallery.cgOnly = e.target.checked; gallery.page = 0; loadGallery(); };
let gallerySearchTimer;
$('gallerySearch').oninput = (e) => {
  clearTimeout(gallerySearchTimer);
  gallerySearchTimer = setTimeout(() => { gallery.search = e.target.value.trim(); gallery.page = 0; loadGallery(); }, 300);
};

// ---------------- 全屏灯箱 ----------------
function openLightbox(i) {
  gallery.lbIndex = i;
  $('lightbox').classList.remove('hidden');
  lbShow();
}
function closeLightbox() {
  $('lightbox').classList.add('hidden');
  gallery.lbIndex = -1;
  $('lbImg').removeAttribute('src');
}
function lbShow() {
  const a = gallery.items[gallery.lbIndex];
  if (!a) return;
  gallery.zoom = 1; gallery.panX = 0; gallery.panY = 0;
  lbApply();
  $('lbName').textContent = a.name;
  $('lbCounter').textContent = `${gallery.lbIndex + 1} / ${gallery.items.length}`;
  $('lbImg').src = `/api/preview/${a.id}`;
}
function lbApply() {
  $('lbImg').style.transform = `translate(${gallery.panX}px, ${gallery.panY}px) scale(${gallery.zoom})`;
  $('lbZoomLabel').textContent = Math.round(gallery.zoom * 100) + '%';
}
function lbNav(dir) {
  const n = gallery.items.length;
  if (!n) return;
  gallery.lbIndex = (gallery.lbIndex + dir + n) % n;
  lbShow();
}
function lbZoom(factor) {
  gallery.zoom = Math.min(8, Math.max(0.2, gallery.zoom * factor));
  lbApply();
}
$('lbClose').onclick = closeLightbox;
$('lbPrev').onclick = () => lbNav(-1);
$('lbNext').onclick = () => lbNav(1);
$('lbZoomIn').onclick = () => lbZoom(1.25);
$('lbZoomOut').onclick = () => lbZoom(0.8);
$('lbZoomReset').onclick = () => { gallery.zoom = 1; gallery.panX = 0; gallery.panY = 0; lbApply(); };
$('lbStage').addEventListener('wheel', (e) => { e.preventDefault(); lbZoom(e.deltaY < 0 ? 1.15 : 0.87); }, { passive: false });
(() => {
  const stage = $('lbStage');
  let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  stage.addEventListener('pointerdown', (e) => {
    dragging = true; sx = e.clientX; sy = e.clientY; ox = gallery.panX; oy = gallery.panY;
    stage.setPointerCapture(e.pointerId); stage.classList.add('grabbing');
  });
  stage.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    gallery.panX = ox + (e.clientX - sx); gallery.panY = oy + (e.clientY - sy);
    lbApply();
  });
  const up = () => { dragging = false; stage.classList.remove('grabbing'); };
  stage.addEventListener('pointerup', up);
  stage.addEventListener('pointercancel', up);
})();
$('lbDownload').onclick = async () => {
  const a = gallery.items[gallery.lbIndex];
  if (!a) return;
  try {
    const blob = await (await api(`/api/preview/${a.id}`)).blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `${a.name}.png`;
    link.click();
    URL.revokeObjectURL(url);
    toast('已下载：' + a.name);
  } catch (e) { toast('下载失败：' + e.message, true); }
};
document.addEventListener('keydown', (e) => {
  if (!gallery.open) return;
  const lbOpen = !$('lightbox').classList.contains('hidden');
  if (e.key === 'Escape') { lbOpen ? closeLightbox() : closeGallery(); return; }
  if (!lbOpen) return;
  if (e.key === 'ArrowLeft') lbNav(-1);
  else if (e.key === 'ArrowRight') lbNav(1);
  else if (e.key === '+' || e.key === '=') lbZoom(1.25);
  else if (e.key === '-') lbZoom(0.8);
});

// 启动时恢复上一次打开的项目
(async () => {
  initDividers();
  initSidebarToggles();
  loadFavorites();
  try {
    const sess = await (await api('/api/session')).json();
    if (sess.active) {
      $('pathInput').value = sess.path;
      if (sess.engine_label) showEngineBadge(sess);
      $('btnScan').disabled = !sess.supported;
      setProject(sess.path);
    }
    const st = await (await api('/api/scan/status')).json();
    if (st.state === 'idle') return;
    $('scanProgress').classList.remove('hidden');
    if (st.asset_count) { renderTypeList(st.type_counts); loadAssets(); $('btnExportAll').disabled = false; $('btnGallery').disabled = false; }
    if (st.state === 'running') { $('btnScan').disabled = true; pollScan(); }
    else $('scanProgress').querySelector('.label').textContent = `完成 · ${st.asset_count} 资源`;
  } catch {}
})();