const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = () => window.pywebview.api;

const state = { settings: {}, jobs: new Map(), order: [], stats: {}, filter: 'all', viewing: null };
const grid = $('#grid');

/* ---------------- helpers ---------------- */
const fmtSecs = s => {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  return m < 60 ? `${m}m ${String(r).padStart(2, '0')}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
};
let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
const icon = d => `<svg viewBox="0 0 24 24"><path d="${d}"/></svg>`;
const ICONS = {
  open: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  redo: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  x: 'M6 6l12 12M18 6 6 18',
};

/* ---------------- settings panel ---------------- */
function paintSettings() {
  const s = state.settings;
  $$('[data-key]').forEach(group => {
    const key = group.dataset.key;
    if (group.type === 'checkbox') { group.checked = !!s[key]; return; }
    $$('button[data-v]', group).forEach(b => b.classList.toggle('on', b.dataset.v === s[key]));
  });
  const sw = $('#colorSw');
  sw.style.setProperty('--c', s.color); sw.classList.toggle('set', s.bg === 'color');
  $('#colorPick').value = s.color || '#ffffff';
  $('#customDir').textContent = s.custom_dir || 'not set';
  $('#customDir').title = s.custom_dir || '';
  const hint = s.format === 'jpg' && s.bg === 'transparent' ? 'JPG has no transparency — white will be used.'
    : s.format === 'webp' ? 'Smaller files, keeps transparency.' : '';
  $('#fmtHint').textContent = hint;
}
async function setSetting(patch) {
  Object.assign(state.settings, patch); paintSettings();
  state.settings = await api().set_settings(patch); paintSettings();
}
$$('.side [data-key]').forEach(group => {
  const key = group.dataset.key;
  if (group.type === 'checkbox') { group.addEventListener('change', () => setSetting({ [key]: group.checked })); return; }
  group.addEventListener('click', async e => {
    const b = e.target.closest('button[data-v]'); if (!b) return;
    if (key === 'dest' && b.dataset.v === 'custom') {
      const r = await api().pick_output(); if (r) { state.settings = r; paintSettings(); }
      else if (state.settings.custom_dir) setSetting({ dest: 'custom' });
      return;
    }
    if (key === 'bg' && b.dataset.v === 'color') return; // handled by the colour input
    setSetting({ [key]: b.dataset.v });
  });
});
$('#colorPick').addEventListener('input', e => setSetting({ bg: 'color', color: e.target.value }));
$('#colorPick').addEventListener('click', () => { if (state.settings.bg !== 'color') setSetting({ bg: 'color' }); });

/* ---------------- adding files ---------------- */
function reportAdd(r) {
  if (!r) return;
  if (r.clipboard === false) return toast('Clipboard has no image');
  if (!r.found) return toast('No supported images found');
  toast(r.added === 1 ? 'Added 1 photo' : `Added ${r.added} photos` + (r.found > r.added ? ` · ${r.found - r.added} already queued` : ''));
}
window.onDropped = reportAdd;
const addFiles = async () => reportAdd(await api().pick_files());
const addFolder = async () => reportAdd(await api().pick_folder());
$('#addFiles').onclick = $('#addFiles2').onclick = addFiles;
$('#addFolder').onclick = $('#addFolder2').onclick = addFolder;
$('#openOut').onclick = () => api().reveal(null);

// drag overlay (Python receives the actual drop with full paths)
let dragDepth = 0;
const overlay = $('#overlay');
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
document.addEventListener('dragenter', e => { if (!hasFiles(e)) return; dragDepth++; overlay.classList.add('show'); });
document.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; overlay.classList.remove('show'); } });
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => { e.preventDefault(); dragDepth = 0; overlay.classList.remove('show'); });

/* ---------------- queue controls ---------------- */
$('#pauseBtn').onclick = () => state.stats.paused ? api().resume() : api().pause();
$('#stopBtn').onclick = () => api().stop();
$('#retryBtn').onclick = () => api().retry();
$('#clearDone').onclick = () => api().clear('done');
$('#clearAll').onclick = () => api().clear('all');
$('#filters').addEventListener('click', e => {
  const b = e.target.closest('button[data-f]'); if (!b) return;
  state.filter = b.dataset.f;
  $$('#filters button[data-f]').forEach(x => x.classList.toggle('on', x === b));
  grid.className = 'grid' + (state.filter === 'all' ? '' : ' f-' + state.filter);
});

/* ---------------- cards ---------------- */
function makeCard(j) {
  const el = document.createElement('div');
  el.className = 'card';
  el.dataset.id = j.id;
  el.innerHTML = `
    <div class="thumb">
      <img class="orig" alt="" hidden>
      <img class="res" alt="" hidden>
      <div class="scan"></div>
      <div class="badge"></div>
      <div class="hover-acts">
        <button data-a="open" title="Open">${icon(ICONS.open)}</button>
        <button data-a="reveal" title="Show in folder">${icon(ICONS.folder)}</button>
        <button data-a="remove" title="Remove from list">${icon(ICONS.x)}</button>
      </div>
    </div>
    <div class="meta"><b></b><span></span></div>`;
  $('.meta b', el).textContent = j.name;
  $('.meta b', el).title = j.name;
  return el;
}
const LABEL = { queued: 'Queued', working: 'Removing…', done: 'Done', error: 'Failed', stopped: 'Stopped' };
function paintCard(j) {
  let el = grid.querySelector(`.card[data-id="${j.id}"]`);
  if (!el) { el = makeCard(j); grid.appendChild(el); }
  el.className = `card ${j.status}`;
  if (j.thumb) { const o = $('.orig', el); o.src = j.thumb; o.hidden = false; }
  if (j.result) { const r = $('.res', el); r.src = j.result; r.hidden = false; }
  $('.badge', el).textContent = LABEL[j.status];
  const sub = $('.meta span', el);
  if (j.status === 'error') { sub.textContent = j.err || 'Failed'; sub.title = j.err; }
  else if (j.status === 'done') sub.textContent = `${(j.ms / 1000).toFixed(1)}s` + (j.size ? ` · ${j.size[0]}×${j.size[1]}` : '');
  else sub.textContent = LABEL[j.status];
  $('[data-a="open"]', el).hidden = $('[data-a="reveal"]', el).hidden = j.status !== 'done';
  $('[data-a="remove"]', el).hidden = j.status === 'working';
}
grid.addEventListener('click', e => {
  const card = e.target.closest('.card'); if (!card) return;
  const id = +card.dataset.id, a = e.target.closest('button[data-a]')?.dataset.a;
  if (a === 'open') return api().open_file(id);
  if (a === 'reveal') return api().reveal(id);
  if (a === 'remove') return api().remove(id);
  const j = state.jobs.get(id);
  if (j?.status === 'done') openViewer(id);
  else if (j?.status === 'error') toast(j.err);
});

/* ---------------- updates from Python ---------------- */
window.onUpdate = ({ jobs, ids, stats }) => {
  for (const j of jobs) {
    const prev = state.jobs.get(j.id) || {};
    const merged = { ...prev, ...j };
    state.jobs.set(j.id, merged);
    paintCard(merged);
  }
  // drop cards that were removed in Python
  const alive = new Set(ids);
  for (const id of [...state.jobs.keys()]) if (!alive.has(id)) {
    state.jobs.delete(id); grid.querySelector(`.card[data-id="${id}"]`)?.remove();
  }
  state.order = ids;
  state.stats = stats;
  paintStats();
};

function paintStats() {
  const s = state.stats;
  const dot = $('#modelDot'), txt = $('#modelText');
  dot.className = 'dot' + (s.model === 'ready' ? ' ready' : s.model?.startsWith('error') ? ' error' : '');
  txt.textContent = s.model === 'ready' ? `${state.settings.model === 'best' ? 'BiRefNet' : 'ISNet'} ready · runs on CPU`
    : s.model === 'loading' ? 'Loading model… (first time downloads it)' : s.model;

  const has = s.total > 0;
  $('#empty').hidden = has;
  $('#progress').hidden = !has;
  if (!has) return;

  const finished = s.done + s.error;
  const pending = s.queued + s.working;
  const active = pending > 0;
  const pct = s.total ? (finished / (finished + pending + s.stopped || 1)) * 100 : 0;
  const fill = $('#fill');
  fill.style.width = `${active ? Math.max(pct, 2) : pct}%`;
  fill.classList.toggle('running', active && !s.paused);
  fill.classList.toggle('complete', !active && s.done > 0 && !s.error && !s.stopped);

  $('#pMain').textContent = active ? `${finished} of ${finished + pending}` : `${s.done} done`;
  let sub;
  if (s.paused && active) sub = 'Paused';
  else if (active) {
    const per = finished ? s.elapsed / finished : 0; // real throughput (2 workers overlap)
    sub = per ? `≈ ${fmtSecs(per * pending)} left · ${per.toFixed(1)}s per photo` : 'Starting…';
  } else {
    sub = [`in ${fmtSecs(s.elapsed)}`, s.error && `${s.error} failed`, s.stopped && `${s.stopped} stopped`].filter(Boolean).join(' · ');
  }
  $('#pSub').textContent = sub;

  $('#pauseBtn').hidden = $('#stopBtn').hidden = !active;
  $('#pauseBtn').textContent = s.paused ? 'Resume' : 'Pause';
  $('#retryBtn').hidden = active || !(s.error || s.stopped);
  $('#retryBtn').textContent = s.error ? 'Retry failed' : 'Resume stopped';
  $('#cAll').textContent = s.total;
  $('#cDone').textContent = s.done;
  $('#cPend').textContent = pending + s.stopped;
  $('#cErr').textContent = s.error;
}

/* ---------------- viewer ---------------- */
const viewer = $('#viewer'), compare = $('#compare'), vBefore = $('#vBefore'), vAfter = $('#vAfter');
let split = 50;
const doneIds = () => state.order.filter(id => state.jobs.get(id)?.status === 'done');

async function openViewer(id) {
  state.viewing = id;
  viewer.hidden = false;
  $('#vLoading').hidden = false;
  vAfter.style.visibility = 'hidden';
  $('#beforeWrap').hidden = $('#handle').hidden = true;
  const j = state.jobs.get(id);
  $('#vName').textContent = j.name;
  $('#vMeta').textContent = `${j.size ? j.size.join(' × ') + ' · ' : ''}${(j.ms / 1000).toFixed(1)}s`;
  const list = doneIds(), i = list.indexOf(id);
  $('#vPrev').disabled = i <= 0; $('#vNext').disabled = i < 0 || i >= list.length - 1;
  const r = await api().preview(id);
  if (state.viewing !== id || !r) return;
  vBefore.src = r.before; vAfter.src = r.after || r.before;
  await Promise.all([vBefore, vAfter].map(im => im.decode().catch(() => {})));
  $('#vLoading').hidden = true;
  vAfter.style.visibility = '';
  // a trimmed result has a different shape: show it alone
  const same = Math.abs(vBefore.naturalWidth / vBefore.naturalHeight - vAfter.naturalWidth / vAfter.naturalHeight) < 0.01;
  compare.classList.toggle('solo', !same);
  $('#beforeWrap').hidden = $('#handle').hidden = !same;
  $$('.tag', compare).forEach(t => t.hidden = !same);
  split = 50; layoutCompare();
}
function layoutCompare() {
  if ($('#beforeWrap').hidden) return;
  const cr = compare.getBoundingClientRect(), ar = vAfter.getBoundingClientRect();
  const wrap = $('#beforeWrap'), x = ar.width * split / 100;
  Object.assign(wrap.style, { left: `${ar.left - cr.left}px`, top: `${ar.top - cr.top}px`, width: `${x}px`, height: `${ar.height}px` });
  Object.assign(vBefore.style, { width: `${ar.width}px`, height: `${ar.height}px`, maxWidth: 'none', maxHeight: 'none' });
  const h = $('#handle');
  Object.assign(h.style, { left: `${ar.left - cr.left + x - 1}px`, top: `${ar.top - cr.top}px`, height: `${ar.height}px`, bottom: 'auto' });
  const tags = $$('.tag', compare);
  tags[0].style.left = `${ar.left - cr.left + 10}px`; tags[0].style.top = tags[1].style.top = `${ar.top - cr.top + 10}px`;
  tags[1].style.left = `${ar.right - cr.left - tags[1].offsetWidth - 10}px`;
}
function dragSplit(e) {
  const ar = vAfter.getBoundingClientRect();
  split = Math.min(100, Math.max(0, ((e.clientX - ar.left) / ar.width) * 100));
  layoutCompare();
}
compare.addEventListener('pointerdown', e => { compare.setPointerCapture(e.pointerId); dragSplit(e); });
compare.addEventListener('pointermove', e => { if (e.buttons) dragSplit(e); });
window.addEventListener('resize', layoutCompare);

function closeViewer() { viewer.hidden = true; state.viewing = null; }
function step(d) {
  const list = doneIds(), i = list.indexOf(state.viewing);
  if (list[i + d] != null) openViewer(list[i + d]);
}
$('#vClose').onclick = closeViewer;
$('#vPrev').onclick = () => step(-1);
$('#vNext').onclick = () => step(1);
$('#vOpen').onclick = () => api().open_file(state.viewing);
$('#vReveal').onclick = () => api().reveal(state.viewing);
$('#vRedo').onclick = () => { api().rerun([state.viewing]); closeViewer(); toast('Re-processing with current settings'); };
$('#vBg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  $$('#vBg button').forEach(x => x.classList.toggle('on', x === b));
  compare.dataset.bg = b.dataset.v;
});
compare.dataset.bg = 'checker';

/* ---------------- keyboard ---------------- */
document.addEventListener('keydown', async e => {
  if (!viewer.hidden) {
    if (e.key === 'Escape') closeViewer();
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
    return;
  }
  if (e.ctrlKey && e.key.toLowerCase() === 'v') { e.preventDefault(); reportAdd(await api().paste()); }
  if (e.ctrlKey && e.key.toLowerCase() === 'o') { e.preventDefault(); addFiles(); }
});

/* ---------------- boot ---------------- */
async function boot() {
  const r = await api().init();
  state.settings = r.settings; state.stats = r.stats;
  paintSettings(); paintStats();
}
if (window.pywebview?.api?.init) boot();
else window.addEventListener('pywebviewready', boot);
