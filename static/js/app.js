/* ============================================================
   夏日告急 — Frontend Application
   Summer Emergency · BUAA Electricity Monitor
   ============================================================ */

(() => {
  'use strict';

  /* ---- Global State ---- */
  const state = {
    currentTab: 'dashboard',
    meters: [],
    systems: [],                 // [{key, name}] available meter systems
    currentSystem: '',           // selected system key in the config form
    meterOptions: null,          // cascade options for the current system
    meterOptionsCache: {},       // { systemKey: options } to avoid refetching
    tier: { is_sponsor: false, poll_interval: 12, max_meters: 1, meter_count: 0 },
    settings: { poll_interval: 12 },
    readings: {},
    chart: null,
    refreshTimer: null,
    selectedMeterForChart: null,
  };

  /* ---- DOM Cache ---- */
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  /* ============================================================
     UTILITIES
     ============================================================ */

  async function api(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: '请求失败' }));
      throw new Error(err.error || err.message || `HTTP ${res.status}`);
    }
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      const json = await res.json();
      if (json && json.success && json.data !== undefined) return json.data;
      return json;
    }
    return null;
  }

  function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function getGaugeColor(remain, threshold) {
    if (remain <= threshold || remain < 5) return 'red';
    if (remain < 20) return 'yellow';
    return 'green';
  }

  function getStatusLabel(remain, threshold) {
    if (remain <= threshold || remain < 5) return { text: '⚠ 电量告急', cls: 'badge-danger' };
    if (remain < 20) return { text: '⚡ 电量偏低', cls: 'badge-warning' };
    return { text: '✓ 电量充足', cls: 'badge-safe' };
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  /* ============================================================
     TOAST NOTIFICATIONS
     ============================================================ */

  function showToast(message, type = 'info') {
    const container = $('#toast-container');
    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${icons[type] || ''}</span><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('toast-out');
      toast.addEventListener('animationend', () => toast.remove());
    }, 3500);
  }

  /* ============================================================
     LOADING OVERLAY
     ============================================================ */

  let loadingCount = 0;

  function showLoading() {
    loadingCount++;
    $('#loading-overlay').classList.remove('hidden');
  }

  function hideLoading() {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount === 0) {
      $('#loading-overlay').classList.add('hidden');
    }
  }

  /* ============================================================
     CONFIRM DIALOG
     ============================================================ */

  function showConfirm(title, message) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'dialog-overlay';
      overlay.innerHTML = `
        <div class="dialog-box">
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(message)}</p>
          <div class="dialog-actions">
            <button class="btn-secondary dialog-cancel">取消</button>
            <button class="btn-danger dialog-confirm">确认</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      overlay.querySelector('.dialog-cancel').onclick = () => { overlay.remove(); resolve(false); };
      overlay.querySelector('.dialog-confirm').onclick = () => { overlay.remove(); resolve(true); };
      overlay.addEventListener('click', (e) => { if (e.target === overlay) { overlay.remove(); resolve(false); } });
    });
  }

  function showPrompt(title, message, placeholder, defaultVal) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'dialog-overlay';
      overlay.innerHTML = `
        <div class="dialog-box">
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(message)}</p>
          <input type="number" class="dialog-input" placeholder="${escapeHtml(placeholder)}" value="${defaultVal || ''}" min="1" step="1">
          <div class="dialog-actions">
            <button class="btn-secondary dialog-cancel">取消</button>
            <button class="btn-primary dialog-confirm">确认</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      const inp = overlay.querySelector('.dialog-input');
      inp.focus();
      inp.select();

      overlay.querySelector('.dialog-cancel').onclick = () => { overlay.remove(); resolve(null); };
      overlay.querySelector('.dialog-confirm').onclick = () => {
        const val = inp.value.trim();
        overlay.remove();
        resolve(val ? Number(val) : null);
      };
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') overlay.querySelector('.dialog-confirm').click();
      });
      overlay.addEventListener('click', (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } });
    });
  }

  /* ============================================================
     TAB NAVIGATION
     ============================================================ */

  function switchTab(tabName) {
    state.currentTab = tabName;

    $$('.nav-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    $$('.tab-content').forEach((sec) => {
      sec.classList.toggle('active', sec.id === `tab-${tabName}`);
    });

    if (tabName === 'dashboard') loadDashboard();
    if (tabName === 'config') loadConfigTab();
    if (tabName === 'settings') loadSettingsTab();
  }

  /* ============================================================
     DASHBOARD
     ============================================================ */

  async function loadDashboard() {
    try {
      await loadMeters();
    } catch (e) {
      showToast('加载电表数据失败: ' + e.message, 'error');
    }
  }

  async function loadMeters() {
    try {
      const meters = await api('GET', '/api/meters');
      state.meters = Array.isArray(meters) ? meters : [];
    } catch (e) {
      console.error('loadMeters error:', e);
      state.meters = [];
      throw e;
    }
    renderDashboard();
  }

  function renderDashboard() {
    const container = $('#meters-container');
    const empty = $('#empty-state');

    if (state.meters.length === 0) {
      container.innerHTML = '';
      empty.classList.remove('hidden');
      hideChartSection();
      return;
    }

    empty.classList.add('hidden');
    container.innerHTML = state.meters.map((m) => renderMeterCard(m)).join('');

    // Attach event listeners
    container.querySelectorAll('[data-action]').forEach((btn) => {
      btn.addEventListener('click', handleMeterAction);
    });

    // Animate gauge fills after render
    requestAnimationFrame(() => {
      container.querySelectorAll('.gauge-fill').forEach((el) => {
        const target = el.dataset.offset;
        if (target !== undefined) {
          el.style.strokeDashoffset = target;
        }
      });
    });
  }

  function renderMeterCard(meter) {
    const latest = meter.latest_reading || {};
    const remain = parseFloat(latest.remain) || 0;
    const threshold = parseFloat(meter.threshold) || 5;
    const color = getGaugeColor(remain, threshold);
    const status = getStatusLabel(remain, threshold);
    const addr = latest.address || meter.address || `${meter.campus || ''} ${meter.building || ''} ${meter.room || ''}`;
    const name = meter.meter_name || addr;
    const readTime = formatTime(latest.reading_time || meter.created_at);
    const price = latest.price != null ? `¥${parseFloat(latest.price).toFixed(2)}/kWh` : '—';
    const payStatus = latest.pay_status || '—';
    const isCritical = color === 'red';

    // Gauge calculations
    const maxKwh = 100;
    const radius = 58;
    const circumference = 2 * Math.PI * radius;
    const pct = Math.min(remain / maxKwh, 1);
    const offset = circumference * (1 - pct);

    return `
      <div class="card meter-card" data-meter-id="${meter.id}">
        <div class="meter-card-header">
          <div>
            <div class="meter-card-title">${escapeHtml(name)}</div>
            <div class="meter-card-subtitle">${escapeHtml(addr)}</div>
          </div>
          <span class="meter-card-badge ${status.cls}">${status.text}</span>
        </div>
        <div class="meter-card-body">
          <div class="gauge-container ${isCritical ? 'critical' : ''}">
            <svg class="gauge-svg" viewBox="0 0 140 140">
              <circle class="gauge-bg" cx="70" cy="70" r="${radius}"/>
              <circle class="gauge-fill gauge-${color}"
                      cx="70" cy="70" r="${radius}"
                      stroke-dasharray="${circumference}"
                      stroke-dashoffset="${circumference}"
                      data-offset="${offset}"/>
            </svg>
            <div class="gauge-center">
              <div class="gauge-value">${remain.toFixed(1)}</div>
              <div class="gauge-unit">kWh</div>
            </div>
          </div>
          <div class="meter-card-info">
            <div class="meter-info-row">
              <span class="meter-info-label">电价</span>
              <span class="meter-info-value">${escapeHtml(price)}</span>
            </div>
            <div class="meter-info-row">
              <span class="meter-info-label">阈值</span>
              <span class="meter-info-value">${threshold.toFixed(0)} kWh</span>
            </div>
            <div class="meter-info-row">
              <span class="meter-info-label">缴费状态</span>
              <span class="meter-info-value">${escapeHtml(payStatus)}</span>
            </div>
            <div class="meter-info-row">
              <span class="meter-info-label">更新时间</span>
              <span class="meter-info-value">${escapeHtml(readTime)}</span>
            </div>
          </div>
        </div>
        <div class="meter-card-actions">
          <button class="btn-secondary btn-sm" data-action="chart" data-meter-id="${meter.id}" data-meter-name="${escapeHtml(name)}">
            📈 趋势
          </button>
          <button class="btn-secondary btn-sm" data-action="charge" data-identity="${meter.identity_no}">
            💳 充值
          </button>
          <button class="btn-secondary btn-sm" data-action="poll">
            🔄 刷新
          </button>
        </div>
      </div>`;
  }

  async function handleMeterAction(e) {
    const btn = e.currentTarget;
    const action = btn.dataset.action;

    try {
      switch (action) {
        case 'chart':
          await showReadingsChart(btn.dataset.meterId, btn.dataset.meterName);
          break;
        case 'charge':
          await chargeMeter(btn.dataset.identity);
          break;
        case 'poll':
          await pollNow();
          break;
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  /* ---- Charge ---- */
  async function chargeMeter(identityNo) {
    if (!identityNo) return showToast('电表信息不完整', 'error');

    const amount = await showPrompt('充值电量', '请输入充值电量（度/kWh）', '50', '50');
    if (!amount || amount <= 0) return;

    // 先同步打开新窗口，防止被浏览器弹窗拦截器拦截
    const newWindow = window.open('about:blank', '_blank');
    if (!newWindow) {
      showToast('弹窗被浏览器拦截，请允许本站弹窗', 'error');
      return;
    }
    newWindow.document.write('<div style="font-family:sans-serif;padding:20px;text-align:center;"><h2>正在生成支付页面，请稍候...</h2></div>');

    showLoading();
    try {
      const res = await api('POST', `/api/charge/${identityNo}`, { amount });
      if (res && res.url) {
        newWindow.location.href = res.url;
        showToast(res.message || '已跳转到充值页面', 'success');
      } else {
        newWindow.close();
        showToast('创建充值订单失败', 'error');
      }
    } catch (e) {
      newWindow.close();
      showToast('充值失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ---- Poll Now ---- */
  async function pollNow() {
    showLoading();
    try {
      await api('POST', '/api/poll-now');
      showToast('正在刷新电表数据…', 'info');
      // Wait a moment for backend to process
      setTimeout(async () => {
        try { await loadMeters(); } catch (_) { /* ignore */ }
        hideLoading();
      }, 2000);
    } catch (e) {
      hideLoading();
      showToast('刷新失败: ' + e.message, 'error');
    }
  }

  /* ---- Readings Chart ---- */
  async function showReadingsChart(meterId, meterName) {
    const section = $('#chart-section');
    section.classList.remove('hidden');
    section.scrollIntoView({ behavior: 'smooth', block: 'center' });

    state.selectedMeterForChart = meterId;

    try {
      const readings = await api('GET', `/api/readings/${meterId}?limit=100`);
      state.readings[meterId] = Array.isArray(readings) ? readings : [];
    } catch (e) {
      showToast('加载历史数据失败', 'error');
      return;
    }

    renderChart(meterId, meterName);
  }

  function hideChartSection() {
    const section = $('#chart-section');
    if (section) section.classList.add('hidden');
    if (state.chart) { state.chart.destroy(); state.chart = null; }
  }

  function renderChart(meterId, meterName) {
    const readings = (state.readings[meterId] || []).slice().reverse();
    if (readings.length === 0) {
      showToast('暂无历史数据', 'warning');
      return;
    }

    const canvas = $('#readings-chart');
    const ctx = canvas.getContext('2d');

    if (state.chart) { state.chart.destroy(); state.chart = null; }

    // Find threshold for this meter
    const meter = state.meters.find((m) => String(m.id) === String(meterId));
    const threshold = meter ? parseFloat(meter.threshold) || 5 : 5;

    const labels = readings.map((r) => formatTime(r.reading_time || r.created_at));
    const data = readings.map((r) => parseFloat(r.remain) || 0);

    // Gradient fill
    const gradient = ctx.createLinearGradient(0, 0, 0, 280);
    gradient.addColorStop(0, 'rgba(255, 107, 53, 0.25)');
    gradient.addColorStop(1, 'rgba(255, 107, 53, 0.0)');

    state.chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: '剩余电量 (kWh)',
            data,
            borderColor: '#ff6b35',
            backgroundColor: gradient,
            borderWidth: 2,
            fill: true,
            tension: 0.35,
            pointBackgroundColor: '#ff6b35',
            pointBorderColor: '#0a0a0f',
            pointBorderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 6,
          },
          {
            label: `阈值 (${threshold} kWh)`,
            data: Array(labels.length).fill(threshold),
            borderColor: 'rgba(239, 68, 68, 0.6)',
            borderWidth: 1.5,
            borderDash: [6, 4],
            fill: false,
            pointRadius: 0,
            pointHoverRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            labels: {
              color: '#888',
              font: { family: "'Inter', sans-serif", size: 12 },
              boxWidth: 12,
              padding: 16,
            },
          },
          tooltip: {
            backgroundColor: 'rgba(18, 18, 26, 0.95)',
            titleColor: '#f0f0f0',
            bodyColor: '#ccc',
            borderColor: 'rgba(255,255,255,0.08)',
            borderWidth: 1,
            cornerRadius: 10,
            padding: 12,
            titleFont: { family: "'Inter', sans-serif" },
            bodyFont: { family: "'Inter', sans-serif" },
          },
        },
        scales: {
          x: {
            ticks: {
              color: '#555',
              font: { size: 10, family: "'Inter', sans-serif" },
              maxRotation: 45,
              maxTicksLimit: 8,
            },
            grid: { color: 'rgba(255,255,255,0.04)' },
          },
          y: {
            ticks: {
              color: '#555',
              font: { size: 11, family: "'Inter', sans-serif" },
              callback: (v) => v + ' kWh',
            },
            grid: { color: 'rgba(255,255,255,0.06)' },
            beginAtZero: true,
          },
        },
      },
    });

    // Show meter name below chart
    let nameEl = section_chart_name();
    if (nameEl) nameEl.textContent = meterName || '';
  }

  function section_chart_name() {
    let el = $('.chart-meter-name');
    if (!el) {
      el = document.createElement('div');
      el.className = 'chart-meter-name';
      $('#chart-section').appendChild(el);
    }
    return el;
  }

  /* ---- Auto-refresh ---- */
  function startAutoRefresh() {
    stopAutoRefresh();
    state.refreshTimer = setInterval(async () => {
      if (state.currentTab === 'dashboard') {
        try { await loadMeters(); } catch (_) { /* silent */ }
      }
    }, 60000);
  }

  function stopAutoRefresh() {
    if (state.refreshTimer) {
      clearInterval(state.refreshTimer);
      state.refreshTimer = null;
    }
  }

  /* ============================================================
     CONFIG TAB
     ============================================================ */

  async function loadConfigTab() {
    try {
      await Promise.all([loadMeterSystems(), loadTier(), loadExistingMeters()]);
    } catch (e) {
      showToast('加载配置失败: ' + e.message, 'error');
    }
    updateTierLimitWarning();
  }

  /* ---- Meter Systems & Options ---- */
  async function loadMeterSystems() {
    if (state.systems && state.systems.length > 0) {
      populateSystemSelect();
      return;
    }
    showLoading();
    try {
      const data = await api('GET', '/api/meter-systems');
      state.systems = Array.isArray(data) ? data : (data && Array.isArray(data.data) ? data.data : []);
      if (state.systems.length === 0) {
        resetSelect('#sel-system', '无可用系统');
        showToast('未获取到任何电表系统', 'error');
        return;
      }
      populateSystemSelect();
    } catch (e) {
      // 关键：失败时也要把占位文字从「正在加载…」改掉，否则会永远卡住
      resetSelect('#sel-system', '加载失败，点此重试');
      showToast('加载系统列表失败：' + (e.message || '网络错误'), 'error');
    } finally {
      hideLoading();
    }
  }

  function populateSystemSelect() {
    const sel = $('#sel-system');
    if (!sel) return;
    sel.disabled = false;
    sel.innerHTML = '<option value="" disabled>请选择系统</option>';
    state.systems.forEach(sys => {
      sel.innerHTML += `<option value="${escapeHtml(sys.key)}">${escapeHtml(sys.name)}</option>`;
    });

    if (state.systems.length > 0) {
      if (!state.currentSystem) {
        state.currentSystem = state.systems[0].key;
      }
      sel.value = state.currentSystem;
      // Load the options for this system
      onSystemChange();
    }
  }

  async function onSystemChange() {
    const sysKey = $('#sel-system')?.value;
    if (!sysKey) return;
    state.currentSystem = sysKey;
    
    // reset dependents
    resetSelect('#sel-campus', '正在加载校区...');
    resetSelect('#sel-building', '请先选择校区');
    resetSelect('#sel-floor', '请先选择楼宇');
    resetSelect('#sel-room', '请先选择楼层');
    resetSelect('#sel-meter', '请先选择房间');

    await loadMeterOptions(sysKey);
  }

  async function loadMeterOptions(sysKey) {
    if (state.meterOptionsCache[sysKey]) {
      state.meterOptions = state.meterOptionsCache[sysKey];
      populateCampusSelect();
      return;
    }
    showLoading();
    try {
      state.meterOptions = await api('GET', `/api/meter-options?system=${sysKey}`);
      state.meterOptionsCache[sysKey] = state.meterOptions;
      populateCampusSelect();
    } catch (e) {
      // 把后端给出的真实原因透出来（如「无法连接到学校服务器」），
      // 而不是笼统的「加载失败」，便于区分是某系统服务不可用还是真 Bug。
      const reason = (e && e.message) ? e.message : '网络错误';
      showToast('该电表系统加载失败：' + reason, 'error');
      resetSelect('#sel-campus', '加载失败（该系统服务不可用）');
    } finally {
      hideLoading();
    }
  }

  function populateCampusSelect() {
    const sel = $('#sel-campus');
    sel.innerHTML = '<option value="">请选择校区</option>';

    // 没有任何校区数据时保持禁用并提示，避免空下拉
    if (!state.meterOptions || Object.keys(state.meterOptions).length === 0) {
      resetSelect('#sel-campus', '该系统暂无可选校区');
      resetSelect('#sel-building', '请先选择校区');
      resetSelect('#sel-floor', '请先选择楼宇');
      resetSelect('#sel-room', '请先选择楼层');
      resetSelect('#sel-meter', '请先选择房间');
      updateSaveButton();
      return;
    }

    // 关键：拿到选项后必须重新启用校区下拉（resetSelect 之前把它设为 disabled），
    // 否则下拉虽被填充却无法点击/选择。其余级联级别（楼/层/房/表）也都各自这样做。
    sel.disabled = false;
    Object.keys(state.meterOptions).forEach((campus) => {
      sel.innerHTML += `<option value="${escapeHtml(campus)}">${escapeHtml(campus)}</option>`;
    });

    // Reset dependent selects
    resetSelect('#sel-building', '请先选择校区');
    resetSelect('#sel-floor', '请先选择楼宇');
    resetSelect('#sel-room', '请先选择楼层');
    resetSelect('#sel-meter', '请先选择房间');
    updateSaveButton();
  }

  function onCampusChange() {
    const campus = $('#sel-campus').value;
    resetSelect('#sel-building', '请选择楼宇');
    resetSelect('#sel-floor', '请先选择楼宇');
    resetSelect('#sel-room', '请先选择楼层');
    resetSelect('#sel-meter', '请先选择房间');

    if (!campus || !state.meterOptions || !state.meterOptions[campus]) {
      $('#sel-building').disabled = true;
      updateSaveButton();
      return;
    }

    const buildings = state.meterOptions[campus];
    const sel = $('#sel-building');
    sel.disabled = false;
    sel.innerHTML = '<option value="">请选择楼宇</option>';
    Object.keys(buildings).forEach((b) => {
      sel.innerHTML += `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`;
    });
    updateSaveButton();
  }

  function onBuildingChange() {
    const campus = $('#sel-campus').value;
    const building = $('#sel-building').value;
    resetSelect('#sel-floor', '请选择楼层');
    resetSelect('#sel-room', '请先选择楼层');
    resetSelect('#sel-meter', '请先选择房间');

    if (!building || !state.meterOptions?.[campus]?.[building]) {
      $('#sel-floor').disabled = true;
      updateSaveButton();
      return;
    }

    const floors = state.meterOptions[campus][building];
    const sel = $('#sel-floor');
    sel.disabled = false;
    sel.innerHTML = '<option value="">请选择楼层</option>';
    Object.keys(floors).forEach((f) => {
      sel.innerHTML += `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`;
    });
    updateSaveButton();
  }

  function onFloorChange() {
    const campus = $('#sel-campus').value;
    const building = $('#sel-building').value;
    const floor = $('#sel-floor').value;
    resetSelect('#sel-room', '请选择房间');
    resetSelect('#sel-meter', '请先选择房间');

    if (!floor || !state.meterOptions?.[campus]?.[building]?.[floor]) {
      $('#sel-room').disabled = true;
      updateSaveButton();
      return;
    }

    const rooms = state.meterOptions[campus][building][floor];
    const sel = $('#sel-room');
    sel.disabled = false;
    sel.innerHTML = '<option value="">请选择房间</option>';
    Object.keys(rooms).forEach((r) => {
      sel.innerHTML += `<option value="${escapeHtml(r)}">${escapeHtml(r)}</option>`;
    });
    updateSaveButton();
  }

  function onRoomChange() {
    const campus = $('#sel-campus').value;
    const building = $('#sel-building').value;
    const floor = $('#sel-floor').value;
    const room = $('#sel-room').value;
    resetSelect('#sel-meter', '请选择电表');

    if (!room || !state.meterOptions?.[campus]?.[building]?.[floor]?.[room]) {
      $('#sel-meter').disabled = true;
      updateSaveButton();
      return;
    }

    const meters = state.meterOptions[campus][building][floor][room];
    const sel = $('#sel-meter');
    sel.disabled = false;
    sel.innerHTML = '<option value="">请选择电表</option>';

    if (Array.isArray(meters)) {
      meters.forEach((m) => {
        const label = m.address || m.meterNo || m.identityNo;
        sel.innerHTML += `<option value="${escapeHtml(m.identityNo)}" data-address="${escapeHtml(m.address || '')}">${escapeHtml(label)}</option>`;
      });
    }
    updateSaveButton();
  }

  function resetSelect(selector, placeholder) {
    const sel = $(selector);
    sel.innerHTML = `<option value="">${placeholder}</option>`;
    sel.disabled = true;
  }

  function updateSaveButton() {
    const meterVal = $('#sel-meter').value;
    const canSave = meterVal && !isTierLimited();
    $('#btn-save-meter').disabled = !canSave;
  }

  function isTierLimited() {
    return state.tier.meter_count >= state.tier.max_meters;
  }

  function updateTierLimitWarning() {
    const warning = $('#tier-limit-warning');
    if (isTierLimited()) {
      const label = state.tier.is_sponsor
        ? `赞助用户最多配置 ${state.tier.max_meters} 个电表`
        : `免费用户最多配置 ${state.tier.max_meters} 个电表`;
      warning.textContent = label;
      warning.classList.remove('hidden');
    } else {
      warning.classList.add('hidden');
    }
    updateSaveButton();
  }

  /* ---- Save Meter ---- */
  async function saveMeter() {
    const campus = $('#sel-campus').value;
    const building = $('#sel-building').value;
    const floor = $('#sel-floor').value;
    const room = $('#sel-room').value;
    const identityNo = $('#sel-meter').value;
    const threshold = parseFloat($('#input-threshold').value) || 5;
    const meterSelect = $('#sel-meter');
    const selectedOption = meterSelect.options[meterSelect.selectedIndex];
    const customName = ($('#input-meter-name')?.value || '').trim();
    const meterName = customName || (selectedOption ? selectedOption.textContent : '');

    if (!identityNo) return showToast('请选择电表', 'warning');

    showLoading();
    try {
      await api('POST', '/api/meters', {
        system: state.currentSystem,
        campus,
        building,
        floor,
        room,
        meter_name: meterName,
        identity_no: identityNo,
        threshold,
      });
      showToast('电表已保存 ✓', 'success');
      // Reset form
      populateCampusSelect();
      const nameInput = $('#input-meter-name');
      if (nameInput) nameInput.value = '';
      // Auto trigger poll so the new meter gets a reading
      await api('POST', '/api/poll-now').catch(() => {});
      // Reload tier count
      await loadTier();
      updateTierLimitWarning();
      await loadExistingMeters();
      await loadMeters();
    } catch (e) {
      showToast('保存失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ---- Existing Meters List ---- */
  async function loadExistingMeters() {
    try {
      const meters = await api('GET', '/api/meters');
      state.meters = Array.isArray(meters) ? meters : [];
    } catch (_) {
      state.meters = [];
    }
    renderExistingMeters();
  }

  function renderExistingMeters() {
    const section = $('#existing-meters');
    const list = $('#meters-list');

    if (state.meters.length === 0) {
      section.classList.add('hidden');
      return;
    }

    section.classList.remove('hidden');
    list.innerHTML = state.meters.map((m) => {
      const name = m.meter_name || m.address || '电表';
      const addr = m.address || `${m.campus || ''} ${m.building || ''} ${m.room || ''}`;
      return `
        <div class="meter-list-item" data-id="${m.id}">
          <div class="meter-list-info">
            <div class="meter-list-name">${escapeHtml(name)}</div>
            <div class="meter-list-detail">${escapeHtml(addr)} · 阈值: ${parseFloat(m.threshold || 5).toFixed(0)} kWh</div>
          </div>
          <div class="meter-list-actions">
            <button class="btn-secondary btn-sm" data-action="edit-threshold" data-id="${m.id}" data-threshold="${m.threshold || 5}">
              ✏️ 阈值
            </button>
            <button class="btn-danger btn-sm" data-action="delete-meter" data-id="${m.id}" data-name="${escapeHtml(name)}">
              🗑️ 删除
            </button>
          </div>
        </div>`;
    }).join('');

    // Event listeners
    list.querySelectorAll('[data-action="edit-threshold"]').forEach((btn) => {
      btn.addEventListener('click', () => editThreshold(btn.dataset.id, btn.dataset.threshold));
    });
    list.querySelectorAll('[data-action="delete-meter"]').forEach((btn) => {
      btn.addEventListener('click', () => deleteMeter(btn.dataset.id, btn.dataset.name));
    });
  }

  async function editThreshold(meterId, currentVal) {
    const val = await showPrompt('修改阈值', '请输入新的告警阈值（kWh）', '5', currentVal);
    if (val === null || val <= 0) return;

    showLoading();
    try {
      await api('PUT', `/api/meters/${meterId}/threshold`, { threshold: val });
      showToast('阈值已更新 ✓', 'success');
      await loadExistingMeters();
    } catch (e) {
      showToast('更新失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  async function deleteMeter(meterId, name) {
    const confirmed = await showConfirm('确认删除', `确定要删除电表「${name}」吗？此操作不可撤销。`);
    if (!confirmed) return;

    showLoading();
    try {
      await api('DELETE', `/api/meters/${meterId}`);
      showToast('电表已删除', 'success');
      await loadTier();
      updateTierLimitWarning();
      await loadExistingMeters();
    } catch (e) {
      showToast('删除失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ============================================================
     SETTINGS TAB
     ============================================================ */

  async function loadSettingsTab() {
    try {
      await Promise.all([loadTier(), loadSettings(), loadServerChanStatus()]);
    } catch (e) {
      showToast('加载设置失败', 'error');
    }
    renderTierDisplay();
    renderPollInterval();
  }

  async function loadTier() {
    try {
      state.tier = await api('GET', '/api/tier');
    } catch (_) {
      /* keep defaults */
    }
  }

  async function loadSettings() {
    try {
      state.settings = await api('GET', '/api/settings');
    } catch (_) {
      /* keep defaults */
    }
  }

  function renderTierDisplay() {
    const container = $('#tier-display');
    if (!container) return;

    const t = state.tier;
    const isSponsor = t.is_sponsor;
    const badgeCls = isSponsor ? 'tier-sponsor' : 'tier-free';
    const badgeText = isSponsor ? '💎 赞助用户' : '🆓 免费用户';

    container.innerHTML = `
      <div class="tier-badge ${badgeCls}">${badgeText}</div>
      <div class="tier-info-grid">
        <div class="tier-info-item">
          <div class="value">${t.meter_count || 0} / ${t.max_meters || 1}</div>
          <div class="label">已用 / 最大电表数</div>
        </div>
        <div class="tier-info-item">
          <div class="value">${t.poll_interval || 12}h</div>
          <div class="label">查询间隔</div>
        </div>
      </div>`;

    const sponsorCard = $('#sponsor-card');
    if (sponsorCard) {
      if (isSponsor) {
        sponsorCard.classList.add('hidden');
      } else {
        sponsorCard.classList.remove('hidden');
      }
    }
  }

  function renderPollInterval() {
    const container = $('#poll-interval-section');
    if (!container) return;

    const intervals = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24];
    const current = state.settings.poll_interval || 12;
    const isSponsor = state.tier.is_sponsor;

    container.innerHTML = `
      <p style="color: var(--text-secondary); font-size: 0.88rem; margin-bottom: 4px;">
        ${isSponsor ? '选择电表查询间隔时间' : '升级为赞助用户可自定义查询间隔'}
      </p>
      <div class="poll-interval-grid">
        ${intervals.map((h) => {
          const isActive = h === current;
          const isDisabled = !isSponsor && h !== 12;
          return `<button class="poll-option ${isActive ? 'active' : ''} ${isDisabled ? 'disabled' : ''}"
                          data-hours="${h}" ${isDisabled ? 'disabled' : ''}>${h}h</button>`;
        }).join('')}
      </div>`;

    container.querySelectorAll('.poll-option:not(.disabled)').forEach((btn) => {
      btn.addEventListener('click', () => updatePollInterval(parseInt(btn.dataset.hours)));
    });
  }

  async function updatePollInterval(hours) {
    showLoading();
    try {
      await api('PUT', '/api/settings', { poll_interval: hours });
      state.settings.poll_interval = hours;
      showToast(`查询间隔已设为 ${hours} 小时 ✓`, 'success');
      renderPollInterval();
      // Reload tier info to reflect change
      await loadTier();
      renderTierDisplay();
    } catch (e) {
      showToast('更新失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ---- Sponsor Activation ---- */
  async function activateSponsor() {
    const code = $('#input-activate-code').value.trim();
    if (!code) return showToast('请输入激活码', 'warning');

    showLoading();
    try {
      const res = await api('POST', '/api/activate', { code });
      if (res && res.success) {
        showToast(res.message || '激活成功！🎉', 'success');
        $('#input-activate-code').value = '';
        await loadTier();
        await loadSettings();
        renderTierDisplay();
        renderPollInterval();
        updateTierLimitWarning();
      } else {
        showToast(res.message || '激活失败', 'error');
      }
    } catch (e) {
      showToast('激活失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ============================================================
     WECHAT PUSH (Server酱)
     ============================================================ */

  function setServerChanBadge(configured) {
    const badge = $('#wechat-badge');
    if (!badge) return;
    badge.textContent = configured ? '已配置' : '未配置';
    badge.className = `status-pill ${configured ? 'on' : 'off'}`;
  }

  /** Pull a Server酱 SendKey out of arbitrary pasted text.
   *  Accepts a bare key or a key embedded in a URL/sentence. */
  function extractSendKey(raw) {
    if (!raw) return '';
    const text = String(raw).trim();
    // ServerChan3 (sctp{uid}t...) or Turbo (SCT...). Keys are alnum.
    const m = text.match(/sctp\d+t[A-Za-z0-9]+/) || text.match(/SCT[A-Za-z0-9]+/);
    return m ? m[0] : '';
  }

  /** Step 1: open the Server酱 SendKey page in a new tab. */
  function openServerChan() {
    window.open('https://sct.ftqq.com/sendkey', '_blank', 'noopener');
    const statusEl = $('#serverchan-status');
    if (statusEl) statusEl.textContent = '在新标签页用微信扫码登录，复制 SendKey 后回到这里点第 2 步';
  }

  /** Step 2: read clipboard → extract key → test → save on success.
   *  Falls back to the manual input field when the clipboard is unreadable
   *  (e.g. a non-secure context on a phone, or permission denied). */
  async function oneClickServerChan() {
    const statusEl = $('#serverchan-status');
    let sendkey = '';

    // Try the clipboard first (the whole point of "one-click").
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const clip = await navigator.clipboard.readText();
        sendkey = extractSendKey(clip);
      }
    } catch (_) {
      /* permission denied / unsupported — fall through to manual */
    }

    // Fall back to whatever is typed in the manual field.
    if (!sendkey) {
      sendkey = extractSendKey($('#input-serverchan-key')?.value || '');
    }

    if (!sendkey) {
      // Reveal the manual section so the user can paste by hand.
      const details = $('#manual-config');
      if (details) details.open = true;
      $('#input-serverchan-key')?.focus();
      showToast('没读到剪贴板里的 SendKey，请在下方手动粘贴后再试', 'warning');
      if (statusEl) statusEl.textContent = '未能自动读取剪贴板，请在「手动输入」中粘贴 SendKey';
      return;
    }

    // Test → save → done, all in one shot.
    showLoading();
    if (statusEl) statusEl.textContent = '已识别 SendKey，正在发送测试推送…';
    try {
      const test = await api('POST', '/api/serverchan/test', { sendkey });
      // test endpoint returns 200 only on success (4xx throws in api()).
      await api('PUT', '/api/serverchan', { sendkey });
      setServerChanBadge(true);
      showToast('微信推送已开启！请查看微信测试消息 🎉', 'success');
      if (statusEl) statusEl.textContent = '已开启，告警将推送到微信。测试消息已发出，请在微信查看';
      const input = $('#input-serverchan-key');
      if (input) input.value = '';
      await loadServerChanStatus();
    } catch (e) {
      showToast('开启失败: ' + e.message, 'error');
      if (statusEl) statusEl.textContent = '失败: ' + e.message + '（请确认 SendKey 复制完整）';
    } finally {
      hideLoading();
    }
  }

  async function loadServerChanStatus() {
    try {
      const data = await api('GET', '/api/serverchan');
      const configured = !!(data && data.configured);
      setServerChanBadge(configured);
      const statusEl = $('#serverchan-status');
      const input = $('#input-serverchan-key');
      if (configured) {
        if (statusEl) statusEl.textContent = `已配置（${data.masked_key}），告警将推送到微信`;
        if (input) input.placeholder = data.masked_key || '已配置';
      } else if (statusEl) {
        statusEl.textContent = '尚未配置，手机将无法通过微信接收提醒';
      }
    } catch (_) {
      /* keep defaults */
    }
  }

  async function saveServerChan() {
    const input = $('#input-serverchan-key');
    const sendkey = (input?.value || '').trim();
    if (!sendkey) return showToast('请输入 SendKey', 'warning');

    showLoading();
    try {
      const res = await api('PUT', '/api/serverchan', { sendkey });
      showToast(res.message || 'SendKey 已保存', 'success');
      if (input) input.value = '';
      await loadServerChanStatus();
    } catch (e) {
      showToast('保存失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  async function testServerChan() {
    // Test the value being typed if present, else the saved key.
    const input = $('#input-serverchan-key');
    const sendkey = (input?.value || '').trim();
    const statusEl = $('#serverchan-status');

    showLoading();
    if (statusEl) statusEl.textContent = '正在发送测试推送…';
    try {
      const res = await api('POST', '/api/serverchan/test', sendkey ? { sendkey } : {});
      showToast(res.message || '测试推送已发送，请查看微信', 'success');
      if (statusEl) statusEl.textContent = '测试推送已发送，请在微信查看';
    } catch (e) {
      showToast('测试失败: ' + e.message, 'error');
      if (statusEl) statusEl.textContent = '测试失败: ' + e.message;
    } finally {
      hideLoading();
    }
  }

  async function clearServerChan() {
    const confirmed = await showConfirm('清除微信推送', '确定要清除已保存的 SendKey 吗？清除后手机将无法通过微信接收提醒。');
    if (!confirmed) return;

    showLoading();
    try {
      await api('PUT', '/api/serverchan', { sendkey: '' });
      showToast('已清除微信推送配置', 'info');
      const input = $('#input-serverchan-key');
      if (input) { input.value = ''; input.placeholder = 'SCT... 或 sctp...'; }
      await loadServerChanStatus();
    } catch (e) {
      showToast('清除失败: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  /* ============================================================
     PUSH NOTIFICATIONS
     ============================================================ */

  // Caches the in-flight registration promise so concurrent callers share one attempt.
  let swRegistrationPromise = null;

  function swSupported() {
    return 'serviceWorker' in navigator && window.isSecureContext;
  }

  /**
   * Reflect push state on the settings status line.
   * @param {'on'|'off'|'pending'|'unsupported'} status (kept for call-site clarity)
   * @param {string} message human-readable detail for the settings line
   */
  function setPushStatusUI(status, message) {
    const msgEl = $('#push-status-msg');
    if (msgEl && message !== undefined) msgEl.textContent = message;
  }

  /**
   * Register /sw.js and resolve only once a worker is active (installing/waiting
   * workers can't be used by PushManager). Returns the active ServiceWorkerRegistration,
   * or null when SW isn't supported. Throws on a genuine registration failure.
   */
  async function ensureServiceWorker() {
    if (!swSupported()) return null;
    if (swRegistrationPromise) return swRegistrationPromise;

    swRegistrationPromise = (async () => {
      // Clean up stale registrations scoped under /static/ from older versions.
      try {
        const registrations = await navigator.serviceWorker.getRegistrations();
        for (const old of registrations) {
          if (old.scope.includes('/static/')) {
            await old.unregister();
            console.log('Unregistered old SW:', old.scope);
          }
        }
      } catch (_) { /* non-fatal */ }

      await navigator.serviceWorker.register('/sw.js');
      // Wait for the worker to reach "activated" so pushManager is usable.
      const reg = await navigator.serviceWorker.ready;
      console.log('SW ready:', reg.scope);
      return reg;
    })();

    try {
      return await swRegistrationPromise;
    } catch (e) {
      swRegistrationPromise = null; // allow a later retry
      console.error('SW registration failed:', e);
      throw e;
    }
  }

  /** Reject after `ms` so a hung network call (e.g. an unreachable push
   *  service like FCM) can't leave the UI stuck in a pending state forever. */
  function withTimeout(promise, ms, label) {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const err = new Error(label || '操作超时');
        err.name = 'TimeoutError';
        reject(err);
      }, ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }

  async function subscribePush() {
    if (!('PushManager' in window) || !('serviceWorker' in navigator) || !('Notification' in window)) {
      showToast('您的设备或浏览器不支持推送功能', 'error');
      setPushStatusUI('unsupported', '设备不支持推送');
      return false;
    }

    if (!window.isSecureContext) {
      showToast('环境受限，无法在手机端开启推送，请在电脑端操作！', 'error');
      setPushStatusUI('unsupported', '当前环境不支持，请在电脑端操作');
      return false;
    }

    try {
      setPushStatusUI('pending', '正在注册推送服务...');

      // Ensure the Service Worker is registered AND active before subscribing.
      const reg = await ensureServiceWorker();
      if (!reg) {
        throw new Error('Service Worker 注册失败');
      }

      // 1) Request notification permission explicitly so we can distinguish a
      //    permission stall from a push-service stall in the status messaging.
      setPushStatusUI('pending', '正在请求通知权限...');
      let permission = Notification.permission;
      if (permission === 'default') {
        permission = await Notification.requestPermission();
      }
      if (permission === 'denied') {
        showToast('通知权限被拒绝，请在浏览器地址栏左侧的站点设置中允许通知', 'error');
        setPushStatusUI('off', '通知权限被拒绝');
        return false;
      }
      if (permission !== 'granted') {
        setPushStatusUI('off', '未授予通知权限');
        return false;
      }

      // 2) Reuse an existing subscription if present (instant, no network).
      let subscription = await reg.pushManager.getSubscription();

      // 3) Otherwise create one. This step contacts the browser vendor's push
      //    service (Chrome/Edge → Google FCM, Firefox → Mozilla). On networks
      //    where that endpoint is unreachable it can hang indefinitely, so we
      //    bound it with a timeout instead of letting the UI freeze.
      if (!subscription) {
        setPushStatusUI('pending', '正在连接推送服务...');

        const keyRes = await api('GET', '/api/vapid-public-key');
        if (!keyRes || !keyRes.public_key) {
          showToast('无法获取推送公钥', 'error');
          setPushStatusUI('off', '获取公钥失败');
          return false;
        }
        const vapidKey = urlBase64ToUint8Array(keyRes.public_key);

        subscription = await withTimeout(
          reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: vapidKey,
          }),
          15000,
          '连接推送服务超时'
        );
      }

      // 4) Persist the subscription on the server.
      await api('POST', '/api/push/subscribe', subscription.toJSON());
      console.log('Push subscription saved');
      showToast('推送通知已开启！', 'success');
      setPushStatusUI('on', '已开启，可实时接收提醒');
      return true;
    } catch (e) {
      if (e.name === 'NotAllowedError') {
        console.log('Push notification permission denied by user');
        showToast('您拒绝了推送权限，请在浏览器设置中开启', 'error');
        setPushStatusUI('off', '权限被拒绝');
      } else if (e.name === 'TimeoutError') {
        // The browser push service (e.g. Google FCM) is unreachable on this
        // network. Desktop Toast notifications still work independently.
        console.warn('Push subscribe timed out:', e);
        showToast('浏览器推送服务连接超时（网络无法访问 Google FCM 等推送服务器）。本机桌面通知不受影响，仍会正常提醒。', 'warning');
        setPushStatusUI('off', '推送服务不可达，已回退为本机桌面通知');
      } else {
        console.error('Push subscription error:', e);
        showToast('开启推送失败: ' + (e.message || '未知错误'), 'error');
        setPushStatusUI('off', '失败: ' + (e.message || '未知错误'));
      }
      return false;
    }
  }

  async function unsubscribePush() {
    try {
      const reg = await navigator.serviceWorker.getRegistration('/sw.js')
        || await navigator.serviceWorker.getRegistration();
      const subscription = reg ? await reg.pushManager.getSubscription() : null;
      if (subscription) {
        await subscription.unsubscribe();
        await api('DELETE', '/api/push/subscribe', { endpoint: subscription.endpoint });
      }
      showToast('推送通知已关闭', 'info');
      setPushStatusUI('off', '未开启');
    } catch (e) {
      console.error('Unsubscribe error:', e);
      showToast('关闭推送失败', 'error');
    }
  }

  async function checkPushStatus() {
    const togglePush = $('#toggle-push');
    if (!togglePush) return;

    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      togglePush.disabled = true;
      setPushStatusUI('unsupported', '当前设备不支持推送');
      return;
    }

    if (!window.isSecureContext) {
      togglePush.disabled = true;
      setPushStatusUI('unsupported', '⚠️ 手机扫码受安全策略限制无法开启，请在电脑端配置。');
      return;
    }

    try {
      const reg = await navigator.serviceWorker.getRegistration('/sw.js')
        || await navigator.serviceWorker.getRegistration();
      if (!reg) {
        togglePush.checked = false;
        setPushStatusUI('off', '未开启');
        return;
      }

      const subscription = await reg.pushManager.getSubscription();
      if (subscription) {
        togglePush.checked = true;
        setPushStatusUI('on', '已开启，可实时接收提醒');
      } else {
        togglePush.checked = false;
        setPushStatusUI('off', '未开启');
      }
    } catch (e) {
      console.error('Check push status error:', e);
    }
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const array = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; ++i) {
      array[i] = raw.charCodeAt(i);
    }
    return array;
  }

  /* ============================================================
     INITIALIZATION
     ============================================================ */

  function bindEvents() {
    // Tab navigation
    $$('.nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Cascading selects
    $('#sel-system')?.addEventListener('change', onSystemChange);
    // 失败重试：占位项被点开时若仍无系统，则重新拉取
    $('#sel-system')?.addEventListener('mousedown', () => {
      if (!state.systems || state.systems.length === 0) loadMeterSystems();
    });
    $('#sel-campus').addEventListener('change', onCampusChange);
    $('#sel-building').addEventListener('change', onBuildingChange);
    $('#sel-floor').addEventListener('change', onFloorChange);
    $('#sel-room').addEventListener('change', onRoomChange);
    $('#sel-meter').addEventListener('change', updateSaveButton);

    // Save meter (form submit — prevents the default page reload)
    const meterForm = $('#form-meter');
    if (meterForm) {
      meterForm.addEventListener('submit', (e) => { e.preventDefault(); saveMeter(); });
    } else {
      $('#btn-save-meter').addEventListener('click', saveMeter);
    }

    // Activate sponsor
    $('#btn-activate')?.addEventListener('click', activateSponsor);
    $('#input-activate-code').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') activateSponsor();
    });

    // Server酱 微信推送
    $('#btn-open-serverchan')?.addEventListener('click', openServerChan);
    $('#btn-oneclick-serverchan')?.addEventListener('click', oneClickServerChan);
    $('#btn-save-serverchan')?.addEventListener('click', saveServerChan);
    $('#btn-test-serverchan')?.addEventListener('click', testServerChan);
    $('#btn-clear-serverchan')?.addEventListener('click', clearServerChan);
    $('#input-serverchan-key')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') saveServerChan();
    });

    // Push toggle binding
    const togglePush = $('#toggle-push');
    if (togglePush) {
      togglePush.addEventListener('change', async (e) => {
        if (e.target.checked) {
          const success = await subscribePush();
          if (!success) e.target.checked = false;
        } else {
          await unsubscribePush();
        }
      });
    }
  }

  async function init() {
    bindEvents();

    // Load initial data
    showLoading();
    try {
      await Promise.all([loadTier(), loadDashboard()]);
    } catch (_) {
      /* handled individually */
    } finally {
      hideLoading();
    }

    // Start auto-refresh
    startAutoRefresh();

    // Register Service Worker (best-effort) and reflect current push status.
    ensureServiceWorker().catch(() => { /* surfaced when user toggles push */ });
    checkPushStatus();
  }

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose to window
  window.app = {
    switchTab,
    chargeMeter,
    pollNow
  };
})();
