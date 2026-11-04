// CraftCore Dashboard Client Controller
let socket = null;
let commandHistory = [];
let historyIndex = -1;
let currentTunnelRunning = false;
let currentServerRunning = false;
let currentPendingRequest = null;
let currentAdminUnlocked = true;

function formatUptime(seconds) {
  if (!seconds || seconds <= 0) return '0s';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

async function loadServerMetrics() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    updateMetricsUI(data);
    const versionLabel=document.getElementById("installed-version");
    if(versionLabel)versionLabel.textContent=data.version || "Not configured";
  } catch (e) {
    console.error('Failed to load server metrics:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initTabs();
  initWebSocket();
  initDropzone();
  loadConfiguration();
  loadPlayers();
  loadWorldInfo();
  loadSecurityData();
  loadBackupsList();
  loadServerMetrics();

  // Fallback heartbeat polling if WebSocket is disconnected
  setInterval(() => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      loadServerMetrics();
      loadSecurityData();
    }
  }, 5000);
  setInterval(loadBackupsList, 30000);

  if (window.initThreeVisualizer) {
    window.initThreeVisualizer();
  }
});

function playAlertTone() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
    osc.frequency.setValueAtTime(880.0, ctx.currentTime + 0.1); // A5
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  } catch(e) {}
}

// Theme Management
function initTheme() {
  document.documentElement.setAttribute('data-theme', 'dark');
}

// Tab Switching
function initTabs() {
  const tabItems = document.querySelectorAll('.tab-item');
  tabItems.forEach(item => {
    item.addEventListener('click', () => {
      tabItems.forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      item.classList.add('active');
      const targetId = item.getAttribute('data-tab');
      const targetPane = document.getElementById(targetId);
      if (targetPane) {
        targetPane.classList.add('active');
        if (targetId === 'tab-map') {
          initMap();
          loadMapData();
        }
      }
    });
  });
}

function switchToSecurityTab() {
  const secTabBtn = document.querySelector('[data-tab="tab-security"]');
  if (secTabBtn) secTabBtn.click();
}

// WebSocket Stream
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;
  
  socket = new WebSocket(wsUrl);

  socket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'metrics') {
        updateMetricsUI(msg.data);
      } else if (msg.type === 'log') {
        appendLogLines(msg.lines);
      } else if (msg.type === 'log_history') {
        renderLogHistory(msg.lines);
      } else if (msg.type === 'cmd_response') {
        if (msg.message) {
          appendLogLines([`[Command Result] ${msg.message}\n`]);
        }
      }
    } catch (e) {
      console.error('[WS] Parse error:', e);
    }
  };

  socket.onclose = () => {
    setTimeout(initWebSocket, 2000);
  };
}

// Update Telemetry & Status Badges
function updateMetricsUI(data) {
  const portLabel=document.getElementById("game-port");
  if(portLabel)portLabel.textContent=data.port || "—";
  const installedVersion=document.getElementById("installed-version");
  if(installedVersion)installedVersion.textContent=data.version || "Not configured";
  currentServerRunning = data.server_running;
  currentTunnelRunning = data.tunnel_running;

  // Status Pills
  const statusDot = document.getElementById('nav-status-dot');
  const statusText = document.getElementById('nav-status-text');

  if (data.server_state === 'running' || data.server_running) {
    statusDot.className = 'pulse-dot online';
    statusDot.style.background = '';
    statusDot.style.boxShadow = '';
    statusText.textContent = 'Server Online';
    document.getElementById('val-status-desc').textContent = 'Server is running smoothly';
    if (window.updateThreeStatus) window.updateThreeStatus('online');
  } else if (data.server_state === 'sleeping' || (!data.server_running && data.gatekeeper_running)) {
    statusDot.className = 'pulse-dot sleeping';
    statusDot.style.background = '#8b5cf6';
    statusDot.style.boxShadow = '0 0 10px rgba(139, 92, 246, 0.6)';
    statusText.textContent = '💤 Server Asleep (Auto-Wake)';
    document.getElementById('val-status-desc').textContent = 'Asleep to save resources • Auto-wakes when a player connects';
    if (window.updateThreeStatus) window.updateThreeStatus('sleeping');
  } else {
    statusDot.className = 'pulse-dot offline';
    statusDot.style.background = '';
    statusDot.style.boxShadow = '';
    statusText.textContent = 'Server Offline';
    document.getElementById('val-status-desc').textContent = 'Server is stopped';
    if (window.updateThreeStatus) window.updateThreeStatus('offline');
  }

  // Tunnel Status
  const navTunnelText = document.getElementById('nav-tunnel-text');
  const tunnelBtnText = document.getElementById('tunnel-btn-text');
  const tunnelAddrDisplay = document.getElementById('tunnel-address-display');
  const summaryTunnelStatus = document.getElementById('summary-tunnel-domain');

  if (data.tunnel_running) {
    navTunnelText.textContent = 'Tunnel: Active';
    tunnelBtnText.textContent = 'Stop Tunnel';
    if (data.tunnel_address) {
      tunnelAddrDisplay.textContent = data.tunnel_address;
      if (summaryTunnelStatus) summaryTunnelStatus.textContent = data.tunnel_address;
    }
  } else {
    navTunnelText.textContent = 'Tunnel: Stopped';
    tunnelBtnText.textContent = 'Start Tunnel';
    tunnelAddrDisplay.textContent = data.tunnel_address || 'Not configured';
  }

  // Metrics: CPU
  const serverCpu = data.server_cpu_percent !== undefined ? Number(data.server_cpu_percent) : 0;
  document.getElementById('val-cpu').innerHTML = `${serverCpu.toFixed(1)}<small>%</small>`;
  document.getElementById('val-sys-cpu').textContent = `System: ${data.system_cpu_percent || 0}%`;
  document.getElementById('bar-cpu').style.width = `${Math.min(serverCpu, 100)}%`;

  // Metrics: RAM
  document.getElementById('val-ram').innerHTML = `${Math.round(data.server_ram_mb || 0)}<small>MB</small>`;
  const sysUsedGb = ((data.system_ram_used_mb || 0) / 1024).toFixed(1);
  const sysTotalGb = ((data.system_ram_total_mb || 0) / 1024).toFixed(1);
  document.getElementById('val-sys-ram').textContent = `System: ${sysUsedGb}/${sysTotalGb} GB`;
  document.getElementById('bar-ram').style.width = `${data.system_ram_percent || 0}%`;

  // Metrics: Uptime & PID
  if (data.server_running && data.server_uptime_seconds > 0) {
    document.getElementById('val-uptime').textContent = formatUptime(data.server_uptime_seconds);
    document.getElementById('val-pid').textContent = `PID: ${data.server_pid}`;
    const desc = document.getElementById('val-status-desc');
    if (desc) desc.textContent = 'Server is running smoothly';
  } else {
    document.getElementById('val-uptime').textContent = 'Offline';
    document.getElementById('val-pid').textContent = 'PID: --';
    const desc = document.getElementById('val-status-desc');
    if (desc) desc.textContent = 'Server is stopped';
  }

  // Metrics: Disk
  const diskUsed = data.disk_used_gb !== undefined ? data.disk_used_gb : 0;
  const diskTotal = data.disk_total_gb !== undefined ? data.disk_total_gb : 0;
  const diskPercent = data.disk_percent !== undefined ? data.disk_percent : 0;
  document.getElementById('val-disk').innerHTML = `${diskUsed}<small>GB</small>`;
  document.getElementById('val-disk-sub').textContent = `${diskPercent}% of ${diskTotal}GB`;
  document.getElementById('bar-disk').style.width = `${diskPercent}%`;

  // Metrics: Hardware Temperatures (CPU & GPU)
  if (data.temperatures) {
    const cpuT = data.temperatures.cpu;
    const gpuT = data.temperatures.gpu;

    if (cpuT && cpuT.current !== null && cpuT.current !== undefined) {
      document.getElementById('val-cpu-temp').innerHTML = `${Number(cpuT.current).toFixed(1)}<small>°C</small>`;
      const minStr = cpuT.min !== null ? `${Number(cpuT.min).toFixed(1)}°C` : '--';
      const maxStr = cpuT.max !== null ? `${Number(cpuT.max).toFixed(1)}°C` : '--';
      document.getElementById('val-cpu-temp-sub').textContent = `Min: ${minStr} | Max: ${maxStr}`;
      
      const cpuPercent = Math.min(100, Math.max(0, ((cpuT.current - 20) / 75) * 100));
      const barCpu = document.getElementById('bar-cpu-temp');
      if (barCpu) barCpu.style.width = `${cpuPercent}%`;
    }

    if (gpuT && gpuT.current !== null && gpuT.current !== undefined) {
      document.getElementById('val-gpu-temp').innerHTML = `${Number(gpuT.current).toFixed(1)}<small>°C</small>`;
      const minStr = gpuT.min !== null ? `${Number(gpuT.min).toFixed(1)}°C` : '--';
      const maxStr = gpuT.max !== null ? `${Number(gpuT.max).toFixed(1)}°C` : '--';
      document.getElementById('val-gpu-temp-sub').textContent = `Min: ${minStr} | Max: ${maxStr}`;
      
      const gpuPercent = Math.min(100, Math.max(0, ((gpuT.current - 20) / 75) * 100));
      const barGpu = document.getElementById('bar-gpu-temp');
      if (barGpu) barGpu.style.width = `${gpuPercent}%`;
    }
  }

  // World Meta if available
  if (data.world) {
    updateWorldUI(data.world);
  }

  // Live Online Players Telemetry (Health, Hunger, Ping)
  if (data.online_players !== undefined) {
    renderOnlinePlayers(data.online_players);
  }

  // Security Metrics & Live Real-Time Push
  if (data.security) {
    renderSecurityUI(data.security);
  }
}

function renderOnlinePlayers(players) {
  const container1 = document.getElementById('live-players-container');
  const container2 = document.getElementById('live-players-container-2');
  const countText1 = document.getElementById('online-players-count-text');
  const countText2 = document.getElementById('online-players-count-text-2');

  const count = players ? players.length : 0;
  if (countText1) countText1.textContent = count;
  if (countText2) countText2.textContent = count;

  const containers = [container1, container2].filter(c => c !== null);
  if (containers.length === 0) return;

  if (!players || players.length === 0) {
    containers.forEach(c => {
      c.innerHTML = `
        <div class="empty-players-box">
          <span class="empty-icon">🎮</span>
          <p>No players currently online on the server.</p>
          <small>Player activity appears here after someone joins.</small>
        </div>
      `;
    });
    return;
  }

  containers.forEach(container => {
    container.innerHTML = '';
    players.forEach(p => {
      const card = document.createElement('div');
      card.className = 'player-telemetry-card';

      const pingClass = p.ping_ms < 60 ? 'good' : (p.ping_ms < 150 ? 'medium' : 'high');
      const pingDot = p.ping_ms < 60 ? '🟢' : (p.ping_ms < 150 ? '🟡' : '🔴');
      const healthPercent = Math.min(100, Math.max(0, (p.health / 20.0) * 100));
      const hungerPercent = Math.min(100, Math.max(0, (p.hunger / 20.0) * 100));
      const avatarUrl = `https://mc-heads.net/avatar/${encodeURIComponent(p.username)}/48`;

      card.innerHTML = `
        <div class="player-card-top">
          <img class="player-head-img" src="${avatarUrl}" alt="${p.username}" onerror="this.src='https://minotar.net/avatar/${encodeURIComponent(p.username)}/48'" />
          <div class="player-card-meta">
            <div class="player-name-row">
              <span class="player-username">${p.username}</span>
              ${p.is_op ? '<span class="badge-type admin" style="font-size:0.65rem;">🛡️ OP</span>' : ''}
            </div>
            <div class="player-sub-info">
              <span class="ping-pill ${pingClass}">${pingDot} ${p.ping_ms} ms</span>
              <span>⏱️ ${formatUptime(p.online_seconds)}</span>
            </div>
          </div>
        </div>

        <div class="player-vitals-box">
          <!-- Health Bar -->
          <div class="vital-row">
            <div class="vital-label-row">
              <span class="vital-title">❤️ Health</span>
              <span class="vital-val">${p.health.toFixed(1)} / 20 HP</span>
            </div>
            <div class="vital-bar-wrap">
              <div class="vital-bar-fill health" style="width: ${healthPercent}%"></div>
            </div>
          </div>

          <!-- Hunger Bar -->
          <div class="vital-row">
            <div class="vital-label-row">
              <span class="vital-title">🍗 Hunger</span>
              <span class="vital-val">${p.hunger} / 20 Food</span>
            </div>
            <div class="vital-bar-wrap">
              <div class="vital-bar-fill hunger" style="width: ${hungerPercent}%"></div>
            </div>
          </div>
        </div>

        <div class="player-card-actions">
          <button class="btn-player-action" style="background:var(--indigo); color:white; border-color:var(--indigo);" onclick="openInventoryModal('${p.username}', '${p.username}')">🎒 View Inventory</button>
          <button class="btn-player-action" onclick="fillCmd('tell ${p.username} Hello!')">💬 Message</button>
          ${p.is_op ? `<button class="btn-player-action" onclick="removeOperator('${p.username}')">De-op</button>` : `<button class="btn-player-action" onclick="addOperator('${p.username}')">👑 Op</button>`}
          <button class="btn-player-action danger" onclick="kickPlayer('${p.username}')">👢 Kick</button>
        </div>
      `;
      container.appendChild(card);
    });
  });
}

function kickPlayer(username) {
  if (confirm(`Are you sure you want to kick '${username}' from the server?`)) {
    fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: `kick ${username} Kicked by Server Admin` })
    }).then(() => {
      showToast(`Kicked player ${username}`, 'info');
    });
  }
}

function renderSecurityUI(data) {
  if (!data) return;

  const pCount = data.pending_count || 0;
  const navBadge = document.getElementById('nav-pending-badge');
  const tabBadge = document.getElementById('tab-security-badge');
  const summaryPending = document.getElementById('summary-pending-count');

  if (pCount > 0) {
    if (navBadge) { navBadge.style.display = 'inline-block'; navBadge.textContent = pCount; }
    if (tabBadge) { tabBadge.style.display = 'inline-block'; tabBadge.textContent = pCount; }
    if (summaryPending) {
      summaryPending.textContent = `${pCount} Pending Approval`;
      summaryPending.className = 'badge-status red';
    }
  } else {
    if (navBadge) navBadge.style.display = 'none';
    if (tabBadge) tabBadge.style.display = 'none';
    if (summaryPending) {
      summaryPending.textContent = 'All Clear (0 Pending)';
      summaryPending.className = 'badge-status';
    }
  }

  // Live sync Admin Session Lock state
  if (data.admin_session_unlocked !== undefined) {
    const isUnlocked = data.admin_session_unlocked;
    currentAdminUnlocked = isUnlocked;
    const adminBadge = document.getElementById('admin-session-badge');
    const adminBtn = document.getElementById('btn-toggle-admin-session');
    const summaryAdminGuard = document.getElementById('summary-admin-guard');
    if (isUnlocked) {
      if (adminBadge) adminBadge.innerHTML = '<span class="dot-green"></span> Session: Unlocked (Ready to Play)';
      if (adminBtn) {
        adminBtn.textContent = '🔒 Lock Admin Session';
        adminBtn.className = 'btn btn-lock-toggle locked';
      }
      if (summaryAdminGuard) summaryAdminGuard.textContent = 'Session Unlocked (Admin Ready)';
    } else {
      if (adminBadge) adminBadge.innerHTML = '<span class="dot-amber"></span> Session: LOCKED (Protected against Impersonation)';
      if (adminBtn) {
        adminBtn.textContent = '🔓 Unlock Admin Session';
        adminBtn.className = 'btn btn-lock-toggle unlocked';
      }
      if (summaryAdminGuard) summaryAdminGuard.textContent = 'Session Locked (Protected)';
    }
  }

  // Set Toggles
  const secWl = document.getElementById('sec-whitelist-toggle');
  if (secWl && data.whitelist_approval_required !== undefined) {
    secWl.checked = data.whitelist_approval_required !== false;
  }
  const secAdmin = document.getElementById('sec-admin-prot-toggle');
  if (secAdmin && data.admin_protection_enabled !== undefined) {
    secAdmin.checked = data.admin_protection_enabled !== false;
  }
  const secSleep = document.getElementById('sec-auto-sleep-toggle');
  if (secSleep && data.auto_sleep_enabled !== undefined) {
    secSleep.checked = data.auto_sleep_enabled !== false;
  }

  // Render Requests Log Table & Live Banner
  const tbody = document.getElementById('security-requests-tbody');
  const logs = data.requests_log || [];

  // Check for pending request to show in top alert banner
  const pendingReq = logs.find(r => r.status === 'pending' || r.status === 'admin_locked');
  const alertBanner = document.getElementById('security-alert-banner');
  const alertActions = document.getElementById('alert-banner-actions');

  if (pendingReq) {
    if (!currentPendingRequest || currentPendingRequest.id !== pendingReq.id) {
      playAlertTone();
    }
    currentPendingRequest = pendingReq;
    if (alertBanner) alertBanner.style.display = 'flex';

    if (pendingReq.status === 'admin_locked') {
      const title = document.getElementById('alert-banner-title');
      if (title) title.innerHTML = `🛡️ Admin Login Intercepted for <strong>${pendingReq.username}</strong>`;
      const desc = document.getElementById('alert-banner-desc');
      if (desc) desc.textContent = `Someone is attempting to join as an Operator. Unlock Admin Session to authorize your login.`;
      if (alertActions) {
        alertActions.innerHTML = `
          <button class="btn btn-approve-banner" onclick="toggleAdminSession(true)">🔓 Unlock & Authorize</button>
          <button class="btn btn-deny-banner" onclick="denyCurrentAlert()">🚫 Block Impersonator</button>
        `;
      }
    } else {
      const title = document.getElementById('alert-banner-title');
      if (title) title.innerHTML = `🚨 Join Request for <strong>${pendingReq.username}</strong>`;
      const desc = document.getElementById('alert-banner-desc');
      if (desc) desc.textContent = `Player is requesting whitelist approval to join the server (received at ${pendingReq.time_str}).`;
      if (alertActions) {
        alertActions.innerHTML = `
          <button class="btn btn-approve-banner" onclick="approveCurrentAlert()">✅ Approve Player</button>
          <button class="btn btn-deny-banner" onclick="denyCurrentAlert()">❌ Reject & Block</button>
        `;
      }
    }
  } else {
    currentPendingRequest = null;
    if (alertBanner) alertBanner.style.display = 'none';
  }

  if (tbody) {
    tbody.innerHTML = '';
    if (logs.length > 0) {
      logs.forEach(req => {
        const tr = document.createElement('tr');
        const statusClass = req.status === 'approved' ? 'approved' : (req.status === 'blocked' ? 'blocked' : 'pending');
        const statusLabel = req.status === 'approved' ? 'Approved' : (req.status === 'blocked' ? 'Blocked' : (req.status === 'admin_locked' ? 'Admin Locked' : 'Pending Review'));
        const typeBadge = req.type === 'admin_attempt' ? '<span class="badge-type admin">🛡️ OP Login</span>' : '<span class="badge-type join">👤 Join Request</span>';

        tr.innerHTML = `
          <td>${req.time_str || '--'}</td>
          <td><strong>${req.username || 'Player'}</strong></td>
          <td>${typeBadge}</td>
          <td><span class="status-chip ${statusClass}">${statusLabel}</span></td>
          <td>
            <div class="table-actions">
              ${req.status !== 'approved' ? `<button class="btn-tb-approve" onclick="approveUser('${req.username}')">Approve</button>` : ''}
              ${req.status !== 'blocked' ? `<button class="btn-tb-deny" onclick="denyUser('${req.username}')">Block</button>` : ''}
              <button class="btn-del" style="margin-left:4px;" onclick="dismissRequest('${req.id || ''}', '${req.username}')">Dismiss</button>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      });
    } else {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-table">No connection requests recorded yet.</td></tr>';
    }
  }

  // Render Approved Users
  const approvedList = document.getElementById('approved-users-list');
  if (approvedList) {
    approvedList.innerHTML = '';
    const approvedUsers = data.approved_users || {};
    const approvedKeys = Object.keys(approvedUsers);
    const appBadge = document.getElementById('approved-count-badge');
    if (appBadge) appBadge.textContent = `${approvedKeys.length} Players`;

    if (approvedKeys.length > 0) {
      approvedKeys.forEach(user => {
        const item = document.createElement('div');
        item.className = 'sec-row';
        const info = approvedUsers[user];
        item.innerHTML = `
          <div>
            👤 <strong>${user}</strong>
            <small style="color:var(--text-dim); margin-left:6px;">${info.note || 'Approved'}</small>
          </div>
          <button class="btn-del" onclick="removeUser('${user}')">Remove</button>
        `;
        approvedList.appendChild(item);
      });
    } else {
      approvedList.innerHTML = '<div class="empty-msg">No players approved yet. Pre-approve above or wait for requests.</div>';
    }
  }

  // Render Blocked Users
  const blockedList = document.getElementById('blocked-users-list');
  if (blockedList) {
    blockedList.innerHTML = '';
    const blockedUsers = data.blocked_users || {};
    const blockedKeys = Object.keys(blockedUsers);
    const blBadge = document.getElementById('blocked-count-badge');
    if (blBadge) blBadge.textContent = `${blockedKeys.length} Players`;

    if (blockedKeys.length > 0) {
      blockedKeys.forEach(user => {
        const item = document.createElement('div');
        item.className = 'sec-row';
        const info = blockedUsers[user];
        item.innerHTML = `
          <div>
            🚫 <strong>${user}</strong>
            <small style="color:var(--red); margin-left:6px;">${info.reason || 'Blocked'}</small>
          </div>
          <button class="btn-del" onclick="removeUser('${user}')">Unblock</button>
        `;
        blockedList.appendChild(item);
      });
    } else {
      blockedList.innerHTML = '<div class="empty-msg">No players currently blocked.</div>';
    }
  }
}

// Security & Whitelist Approvals Manager (Initial / On-Demand Fetch)
async function loadSecurityData() {
  try {
    const res = await fetch('/api/security/status');
    const data = await res.json();
    renderSecurityUI(data);
  } catch (e) {
    console.error('Failed to load security data:', e);
  }
}

async function toggleAdminSession(forceUnlock = false) {
  const nextState = forceUnlock ? true : !currentAdminUnlocked;
  try {
    const res = await fetch('/api/security/toggle_admin_session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ unlocked: nextState })
    });
    const data = await res.json();
    if (data.success) {
      showToast(data.message, 'success');
      loadSecurityData();
    }
  } catch (e) {
    showToast('Failed to toggle admin session', 'error');
  }
}

async function approveUser(username, note) {
  try {
    const res = await fetch('/api/security/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, note: note || 'Approved via Dashboard' })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Player '${username}' approved! They can now join the server.`, 'success');
      loadSecurityData();
    } else {
      showToast(data.error || 'Failed to approve player', 'error');
    }
  } catch (e) {
    showToast('Failed to approve player', 'error');
  }
}

async function denyUser(username) {
  try {
    const res = await fetch('/api/security/deny', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, reason: 'Blocked by Admin' })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Player '${username}' blocked from connecting`, 'info');
      loadSecurityData();
    } else {
      showToast(data.error || 'Failed to block player', 'error');
    }
  } catch (e) {
    showToast('Failed to block player', 'error');
  }
}

async function dismissRequest(id, username) {
  try {
    const res = await fetch('/api/security/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, username })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Request dismissed from activity log', 'info');
      loadSecurityData();
    } else {
      showToast(data.error || 'Failed to dismiss', 'error');
    }
  } catch (e) {
    showToast('Failed to dismiss request', 'error');
  }
}

async function removeUser(username) {
  try {
    const res = await fetch('/api/security/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Player '${username}' removed from list`, 'info');
      loadSecurityData();
    }
  } catch (e) {
    showToast('Failed to remove player', 'error');
  }
}

function manualApproveUser() {
  const userInput = document.getElementById('manual-user-input');
  const noteInput = document.getElementById('manual-user-note');
  const username = userInput.value.trim();
  const note = noteInput.value.trim() || 'Manual Pre-Approval';
  if (!username) {
    showToast('Please enter a Minecraft username', 'error');
    return;
  }
  approveUser(username, note);
  userInput.value = '';
  noteInput.value = '';
}

function approveCurrentAlert() {
  if (currentPendingRequest) {
    approveUser(currentPendingRequest.username);
  }
}

function denyCurrentAlert() {
  if (currentPendingRequest) {
    denyUser(currentPendingRequest.username);
  }
}

async function saveSecurityToggle() {
  const whitelist_approval_required = document.getElementById('sec-whitelist-toggle').checked;
  const admin_protection_enabled = document.getElementById('sec-admin-prot-toggle').checked;
  const auto_sleep_el = document.getElementById('sec-auto-sleep-toggle');
  const auto_sleep_enabled = auto_sleep_el ? auto_sleep_el.checked : true;

  try {
    const res = await fetch('/api/security/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ whitelist_approval_required, admin_protection_enabled, auto_sleep_enabled })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Security & sleep rules updated!', 'success');
    }
  } catch (e) {
    showToast('Failed to update security toggles', 'error');
  }
}

// World Management (Export & Import)
async function loadWorldInfo() {
  try {
    const res = await fetch('/api/world/info');
    const data = await res.json();
    updateWorldUI(data);
  } catch (e) {
    console.error('Failed to load world info:', e);
  }
}

function updateWorldUI(w) {
  if (!w) return;
  document.getElementById('world-meta-name').textContent = w.name || 'world';
  document.getElementById('world-meta-size').textContent = `${w.size_mb} MB`;
  document.getElementById('world-meta-files').textContent = `${w.file_count} files`;
  document.getElementById('world-meta-time').textContent = w.last_modified;

  document.getElementById('summary-world-name').textContent = w.name || 'world';
  document.getElementById('summary-world-size').textContent = `${w.size_mb} MB`;

  if (w.backups) {
    renderBackupsUI({
      backups: w.backups,
      last_backup_str: w.last_backup_str,
      next_backup_in_seconds: w.next_backup_in_seconds
    });
  }
}

// Automated Backups Management
async function loadBackupsList() {
  try {
    const res = await fetch('/api/backups');
    const data = await res.json();
    renderBackupsUI(data);
  } catch (e) {
    console.error('Failed to load backups list:', e);
  }
}

function renderBackupsUI(data) {
  if (!data) return;

  const tbody = document.getElementById('backups-list-tbody');
  const countdownEl = document.getElementById('backup-next-countdown');
  const lastStrEl = document.getElementById('backup-last-str');

  if (lastStrEl && data.last_backup_str) {
    lastStrEl.textContent = `Last backup: ${data.last_backup_str}`;
  }

  if (countdownEl && data.next_backup_in_seconds !== undefined) {
    if (data.next_backup_in_seconds > 0) {
      const hours = Math.floor(data.next_backup_in_seconds / 3600);
      const mins = Math.floor((data.next_backup_in_seconds % 3600) / 60);
      countdownEl.textContent = `⏳ Next automated backup in: ${hours}h ${mins}m`;
    } else {
      countdownEl.textContent = `⏳ Next automated backup: Due soon / Ready`;
    }
  }

  if (!tbody) return;

  const backups = data.backups || [];
  if (backups.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-table">No backup copies stored yet. Backups occur every 12 hours automatically or click "Create Backup Now" above.</td></tr>';
    return;
  }

  tbody.innerHTML = '';
  backups.forEach(b => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>📦 ${b.filename}</strong></td>
      <td>${b.created_at_str}</td>
      <td><span class="badge-status">${b.size_mb} MB</span></td>
      <td style="text-align: right;">
        <div class="table-actions" style="justify-content: flex-end;">
          <button class="btn-tb-approve" onclick="downloadBackupFile('${b.filename}')">⬇️ Download</button>
          <button class="btn-tb-deny" style="background:var(--indigo); border-color:var(--indigo);" onclick="restoreBackupFile('${b.filename}')">♻️ Restore</button>
          <button class="btn-del" onclick="deleteBackupFile('${b.filename}')">🗑️ Delete</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function downloadBackupFile(filename) {
  showToast(`Downloading ${filename}...`, 'info');
  window.location.href = `/api/backups/download?file=${encodeURIComponent(filename)}`;
}

async function triggerManualBackup() {
  showToast('Creating world backup snapshot...', 'info');
  try {
    const res = await fetch('/api/backups/create', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      showToast('✅ World backup created successfully!', 'success');
      loadBackupsList();
      loadWorldInfo();
    } else {
      showToast(data.error || 'Backup creation failed', 'error');
    }
  } catch (e) {
    showToast('Failed to create backup', 'error');
  }
}

async function restoreBackupFile(filename) {
  if (!confirm(`Are you sure you want to restore the world from '${filename}'?\n\nThis will temporarily stop the server and restore the world to this snapshot.`)) {
    return;
  }
  showToast(`Restoring world from ${filename}...`, 'info');
  try {
    const res = await fetch('/api/backups/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    const data = await res.json();
    if (data.success) {
      showToast('✅ World restored successfully! Server restarting.', 'success');
      loadWorldInfo();
      loadBackupsList();
    } else {
      showToast(data.error || 'Restore failed', 'error');
    }
  } catch (e) {
    showToast('Failed to restore backup', 'error');
  }
}

async function deleteBackupFile(filename) {
  if (!confirm(`Are you sure you want to delete '${filename}'?`)) {
    return;
  }
  try {
    const res = await fetch('/api/backups/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Deleted ${filename}`, 'info');
      loadBackupsList();
    } else {
      showToast(data.error || 'Delete failed', 'error');
    }
  } catch (e) {
    showToast('Failed to delete backup', 'error');
  }
}

// Export / Download World Backup (.zip)
function exportWorldBackup() {
  showToast('Generating world backup zip archive...', 'info');
  window.location.href = '/api/world/export';
}

// Drag & Drop World ZIP Upload
function initDropzone() {
  const dropzone = document.getElementById('world-dropzone');
  if (!dropzone) return;

  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('dragover');
    }, false);
  });

  dropzone.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      uploadWorldZip(files[0]);
    }
  });
}

function handleFileSelected(e) {
  const files = e.target.files;
  if (files.length > 0) {
    uploadWorldZip(files[0]);
  }
}

function uploadWorldZip(file) {
  if (!file.name.endsWith('.zip')) {
    showToast('Please upload a valid .zip file containing a Minecraft world', 'error');
    return;
  }

  const confirmMsg = `Are you sure you want to upload "${file.name}"? This will replace the active world folder. (A safety backup of the old world is kept).`;
  if (!confirm(confirmMsg)) return;

  const progressContainer = document.getElementById('upload-progress-container');
  const progressBar = document.getElementById('upload-progress-bar');
  const progressText = document.getElementById('upload-percentage');
  const filenameText = document.getElementById('upload-filename');
  const statusText = document.getElementById('upload-status-text');

  filenameText.textContent = `${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
  progressContainer.style.display = 'flex';
  progressBar.style.width = '0%';
  progressText.textContent = '0%';
  statusText.textContent = 'Uploading to server...';

  const formData = new FormData();
  formData.append('world_zip', file);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/world/import', true);
  const csrf = document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
  if (csrf) xhr.setRequestHeader('X-XSRFToken', decodeURIComponent(csrf[1]));

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const percent = Math.round((e.loaded / e.total) * 100);
      progressBar.style.width = `${percent}%`;
      progressText.textContent = `${percent}%`;
      if (percent === 100) {
        statusText.textContent = 'Unpacking world files on server...';
      }
    }
  };

  xhr.onload = () => {
    if (xhr.status === 200) {
      try {
        const res = JSON.parse(xhr.responseText);
        if (res.success) {
          showToast('World restored successfully! You can now join your new world.', 'success');
          statusText.textContent = 'Done! World restored successfully.';
          if (res.world) updateWorldUI(res.world);
        } else {
          showToast(res.error || 'Failed to extract world', 'error');
          statusText.textContent = `Error: ${res.error}`;
        }
      } catch (e) {
        showToast('World uploaded, checking status...', 'info');
      }
    } else {
      showToast('Error uploading world zip file', 'error');
      statusText.textContent = 'Upload failed. Please check file format.';
    }
  };

  xhr.onerror = () => {
    showToast('Network error during upload', 'error');
    statusText.textContent = 'Network error occurred.';
  };

  xhr.send(formData);
}

// Console Rendering & Colors
function parseMinecraftColors(text) {
  const colorMap = {
    '§0': '#000000', '§1': '#0000aa', '§2': '#00aa00', '§3': '#00aaaa',
    '§4': '#aa0000', '§5': '#aa00aa', '§6': '#ffaa00', '§7': '#aaaaaa',
    '§8': '#555555', '§9': '#5555ff', '§a': '#55ff55', '§b': '#55ffff',
    '§c': '#ff5555', '§d': '#ff55ff', '§e': '#ffff55', '§f': '#ffffff'
  };

  let escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  for (const [code, hex] of Object.entries(colorMap)) {
    const regex = new RegExp(code, 'g');
    escaped = escaped.replace(regex, `<span style="color: ${hex}">`);
  }
  escaped = escaped.replace(/§r/g, '</span>');
  return escaped;
}

function renderLogHistory(lines) {
  const term = document.getElementById('terminal-output');
  term.innerHTML = '';
  lines.forEach(line => {
    const div = document.createElement('div');
    div.className = 'terminal-line';
    div.innerHTML = parseMinecraftColors(line.trim());
    term.appendChild(div);
  });
  term.scrollTop = term.scrollHeight;
}

function appendLogLines(lines) {
  const term = document.getElementById('terminal-output');
  lines.forEach(line => {
    if (!line.trim()) return;
    const div = document.createElement('div');
    div.className = 'terminal-line';
    div.innerHTML = parseMinecraftColors(line.trim());
    term.appendChild(div);
  });
  term.scrollTop = term.scrollHeight;
}

function clearConsole() {
  document.getElementById('terminal-output').innerHTML = '<div class="terminal-line system">[Console Cleared]</div>';
}

function fillCmd(cmd) {
  document.getElementById('cmd-input').value = cmd;
  document.getElementById('cmd-input').focus();
}

function handleCommandSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('cmd-input');
  const cmd = input.value.trim();
  if (!cmd) return;

  commandHistory.push(cmd);
  historyIndex = commandHistory.length;

  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'cmd', cmd }));
    appendLogLines([`> ${cmd}\n`]);
  } else {
    fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd })
    });
  }

  input.value = '';
}

// Arrow Up / Down command history
document.getElementById('cmd-input').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowUp') {
    if (historyIndex > 0) {
      historyIndex--;
      e.target.value = commandHistory[historyIndex];
    }
  } else if (e.key === 'ArrowDown') {
    if (historyIndex < commandHistory.length - 1) {
      historyIndex++;
      e.target.value = commandHistory[historyIndex];
    } else {
      historyIndex = commandHistory.length;
      e.target.value = '';
    }
  }
});

// Load & Save Configuration
async function loadConfiguration() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();

    document.getElementById('cfg-motd').value = cfg['motd'] || '';
    document.getElementById('cfg-gamemode').value = cfg['gamemode'] || 'survival';
    document.getElementById('cfg-difficulty').value = cfg['difficulty'] || 'normal';
    document.getElementById('cfg-max-players').value = cfg['max-players'] || '20';
    document.getElementById('cfg-view-distance').value = cfg['view-distance'] || '10';
    document.getElementById('cfg-simulation-distance').value = cfg['simulation-distance'] || '6';
    const sleepEl = document.getElementById('cfg-sleeping-percentage');
    if (sleepEl) sleepEl.value = cfg['players-sleeping-percentage'] || '50';

    const maxPlayersSummary=document.getElementById('summary-max-players');
    if(maxPlayersSummary)maxPlayersSummary.textContent = cfg['max-players'] || '20';

    document.getElementById('cfg-pvp').checked = cfg['pvp'] === 'true';
    document.getElementById('cfg-allow-flight').checked = cfg['allow-flight'] === 'true';
    document.getElementById('cfg-white-list').checked = cfg['white-list'] === 'true';
    document.getElementById('cfg-online-mode').checked = cfg['online-mode'] === 'true';
    document.getElementById('cfg-spawn-monsters').checked = cfg['spawn-monsters'] === 'true';
    document.getElementById('cfg-spawn-animals').checked = cfg['spawn-animals'] === 'true';
  } catch (e) {
    console.error('Failed to load properties:', e);
  }
}

async function saveConfiguration() {
  const sleepInput = document.getElementById('cfg-sleeping-percentage');
  const payload = {
    'motd': document.getElementById('cfg-motd').value,
    'gamemode': document.getElementById('cfg-gamemode').value,
    'difficulty': document.getElementById('cfg-difficulty').value,
    'max-players': document.getElementById('cfg-max-players').value,
    'view-distance': document.getElementById('cfg-view-distance').value,
    'simulation-distance': document.getElementById('cfg-simulation-distance').value,
    'players-sleeping-percentage': sleepInput ? sleepInput.value : '50',
    'pvp': document.getElementById('cfg-pvp').checked ? 'true' : 'false',
    'allow-flight': document.getElementById('cfg-allow-flight').checked ? 'true' : 'false',
    'white-list': document.getElementById('cfg-white-list').checked ? 'true' : 'false',
    'online-mode': document.getElementById('cfg-online-mode').checked ? 'true' : 'false',
    'spawn-monsters': document.getElementById('cfg-spawn-monsters').checked ? 'true' : 'false',
    'spawn-animals': document.getElementById('cfg-spawn-animals').checked ? 'true' : 'false',
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      showToast('Settings saved! Restart server to apply changes.', 'success');
      loadConfiguration();
    } else {
      showToast(data.error || 'Failed to save settings', 'error');
    }
  } catch (e) {
    showToast('Failed to save configuration', 'error');
  }
}

// Players & Admins Manager
async function loadPlayers() {
  try {
    const res = await fetch('/api/players');
    const data = await res.json();

    const opsList = document.getElementById('ops-list');
    if (opsList) {
      opsList.innerHTML = '';
      if (data.ops && data.ops.length > 0) {
        data.ops.forEach(op => {
          const item = document.createElement('div');
          item.className = 'player-row';
          item.innerHTML = `
            <span>🛡️ <strong>${op.name}</strong> <small style="color:var(--text-dim)">(Level ${op.level})</small></span>
            <button class="btn-del" onclick="removeOperator('${op.name}')">Remove</button>
          `;
          opsList.appendChild(item);
        });
      } else {
        opsList.innerHTML = '<div class="empty-msg">No Operators added yet.</div>';
      }
    }

    const wlList = document.getElementById('whitelist-list');
    if (wlList) {
      wlList.innerHTML = '';
      if (data.whitelist && data.whitelist.length > 0) {
        data.whitelist.forEach(wl => {
          const item = document.createElement('div');
          item.className = 'player-row';
          item.innerHTML = `
            <span>👤 <strong>${wl.name}</strong></span>
            <button class="btn-del" onclick="removeWhitelist('${wl.name}')">Remove</button>
          `;
          wlList.appendChild(item);
        });
      } else {
        wlList.innerHTML = '<div class="empty-msg">Whitelist is currently open to everyone.</div>';
      }
    }

    const allPlayersList = document.getElementById('all-players-list');
    const allCountBadge = document.getElementById('all-players-count');
    if (allPlayersList) {
      allPlayersList.innerHTML = '';
      const allPlayers = data.all_players || [];
      if (allCountBadge) allCountBadge.textContent = allPlayers.length;

      if (allPlayers.length > 0) {
        allPlayers.forEach(p => {
          const isOp = data.ops && data.ops.some(op => op.name.toLowerCase() === p.name.toLowerCase());
          const isWl = data.whitelist && data.whitelist.some(wl => wl.name.toLowerCase() === p.name.toLowerCase());
          const item = document.createElement('div');
          item.className = 'player-row';
          item.style.padding = '10px 14px';
          item.innerHTML = `
            <div style="display: flex; align-items: center; gap: 12px;">
              <img src="https://mc-heads.net/avatar/${p.name}/32" alt="${p.name}" style="width: 32px; height: 32px; border-radius: 6px; image-rendering: pixelated; border: 1px solid var(--border);" onerror="this.src='https://mc-heads.net/avatar/MHF_Steve/32'">
              <div>
                <div style="display: flex; align-items: center; gap: 6px;">
                  <strong>${p.name}</strong>
                  ${isOp ? '<span class="badge-type admin" style="padding: 2px 6px; font-size: 0.68rem;">🛡️ OP</span>' : ''}
                  ${isWl ? '<span class="badge-type join" style="padding: 2px 6px; font-size: 0.68rem;">📋 Whitelisted</span>' : ''}
                </div>
                <small style="color: var(--text-dim); font-family: monospace; font-size: 0.7rem;">${p.uuid}</small>
              </div>
            </div>
            <button class="btn btn-sm btn-primary" style="padding: 6px 14px; font-size: 0.78rem; background: var(--indigo); border-color: var(--indigo);" onclick="openInventoryModal('${p.uuid}', '${p.name}')">🎒 Inspect Inventory</button>
          `;
          allPlayersList.appendChild(item);
        });
      } else {
        allPlayersList.innerHTML = '<div class="empty-msg">No player records found. Players will automatically appear here once they connect.</div>';
      }
    }
  } catch (e) {
    console.error('Failed to load players:', e);
  }
}

function addOperator() {
  const input = document.getElementById('input-op-name');
  const name = input.value.trim();
  if (!name) return;
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: `op ${name}` })
  }).then(() => {
    showToast(`OP command sent for ${name}`, 'success');
    input.value = '';
    setTimeout(loadPlayers, 1000);
  });
}

function removeOperator(name) {
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: `deop ${name}` })
  }).then(() => {
    showToast(`De-op command sent for ${name}`, 'info');
    setTimeout(loadPlayers, 1000);
  });
}

function addWhitelist() {
  const input = document.getElementById('input-wl-name');
  const name = input.value.trim();
  if (!name) return;
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: `whitelist add ${name}` })
  }).then(() => {
    showToast(`Added ${name} to whitelist`, 'success');
    input.value = '';
    setTimeout(loadPlayers, 1000);
  });
}

function removeWhitelist(name) {
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: `whitelist remove ${name}` })
  }).then(() => {
    showToast(`Removed ${name} from whitelist`, 'info');
    setTimeout(loadPlayers, 1000);
  });
}

// ==========================================================================
// MINECRAFT PLAYER INVENTORY & ENDER CHEST INSPECTOR
// ==========================================================================
let currentInspectedPlayer = null;

async function openInventoryModal(uuidOrName, displayName) {
  const modal = document.getElementById('inv-modal');
  if (!modal) return;

  const targetName = displayName || uuidOrName;
  document.getElementById('inv-player-avatar').src = `https://mc-heads.net/avatar/${encodeURIComponent(targetName)}/44`;
  document.getElementById('inv-player-name').textContent = targetName;
  document.getElementById('inv-player-uuid').textContent = `Loading player data...`;
  
  modal.style.display = 'flex';
  switchInvModalTab('main');

  try {
    const res = await fetch(`/api/players/inventory?uuid=${encodeURIComponent(uuidOrName)}`);
    const data = await res.json();
    if (data.success && data.player) {
      renderInventoryModalData(data.player);
    } else {
      showToast(data.error || 'Player inventory not found', 'error');
    }
  } catch (e) {
    showToast('Failed to load player inventory', 'error');
  }
}

function closeInventoryModal(event) {
  const modal = document.getElementById('inv-modal');
  if (modal) modal.style.display = 'none';
  hideItemTooltip();
}

function switchInvModalTab(tab) {
  const btnMain = document.getElementById('inv-tab-btn-main');
  const btnEnder = document.getElementById('inv-tab-btn-ender');
  const contentMain = document.getElementById('inv-tab-content-main');
  const contentEnder = document.getElementById('inv-tab-content-ender');

  if (tab === 'main') {
    btnMain.classList.add('active');
    btnEnder.classList.remove('active');
    contentMain.style.display = 'block';
    contentEnder.style.display = 'none';
  } else {
    btnMain.classList.remove('active');
    btnEnder.classList.add('active');
    contentMain.style.display = 'none';
    contentEnder.style.display = 'block';
  }
}

function renderInventoryModalData(player) {
  currentInspectedPlayer = player;
  document.getElementById('inv-player-name').textContent = player.username;
  document.getElementById('inv-player-uuid').textContent = `UUID: ${player.uuid}`;
  document.getElementById('inv-player-avatar').src = `https://mc-heads.net/avatar/${encodeURIComponent(player.username)}/44`;

  document.getElementById('inv-health-badge').textContent = `❤️ ${player.health} / 20 Health`;
  document.getElementById('inv-food-badge').textContent = `🍗 ${player.food} / 20 Food`;
  document.getElementById('inv-xp-badge').textContent = `🌟 Level ${player.xp_level} (${player.xp_total} XP)`;
  document.getElementById('inv-pos-badge').textContent = `📍 ${player.dimension} [${player.pos.x}, ${player.pos.y}, ${player.pos.z}]`;

  // Armor & Offhand
  renderSlotItem(document.getElementById('slot-armor-helmet'), player.armor.helmet, '🪖');
  renderSlotItem(document.getElementById('slot-armor-chestplate'), player.armor.chestplate, '👕');
  renderSlotItem(document.getElementById('slot-armor-leggings'), player.armor.leggings, '👖');
  renderSlotItem(document.getElementById('slot-armor-boots'), player.armor.boots, '🥾');
  renderSlotItem(document.getElementById('slot-offhand'), player.offhand, '🛡️');

  // Main Inventory (27 Slots)
  const mainGrid = document.getElementById('inv-grid-main');
  mainGrid.innerHTML = '';
  for (let i = 0; i < 27; i++) {
    const slotDiv = document.createElement('div');
    slotDiv.className = 'inv-slot';
    renderSlotItem(slotDiv, player.main_inventory[i]);
    mainGrid.appendChild(slotDiv);
  }

  // Hotbar (9 Slots)
  const hotbarGrid = document.getElementById('inv-grid-hotbar');
  hotbarGrid.innerHTML = '';
  for (let i = 0; i < 9; i++) {
    const slotDiv = document.createElement('div');
    slotDiv.className = 'inv-slot';
    renderSlotItem(slotDiv, player.hotbar[i]);
    hotbarGrid.appendChild(slotDiv);
  }

  // Ender Chest (27 Slots)
  const enderGrid = document.getElementById('inv-grid-ender');
  enderGrid.innerHTML = '';
  for (let i = 0; i < 27; i++) {
    const slotDiv = document.createElement('div');
    slotDiv.className = 'inv-slot';
    renderSlotItem(slotDiv, player.ender_chest[i]);
    enderGrid.appendChild(slotDiv);
  }
}

function getItemIconEmoji(itemId) {
  if (!itemId) return '📦';
  const id = itemId.toLowerCase();
  if (id.includes('sword')) return '🗡️';
  if (id.includes('pickaxe')) return '⛏️';
  if (id.includes('axe')) return '🪓';
  if (id.includes('shovel')) return '🥄';
  if (id.includes('hoe')) return '🌾';
  if (id.includes('bow') || id.includes('crossbow')) return '🏹';
  if (id.includes('helmet')) return '🪖';
  if (id.includes('chestplate')) return '👕';
  if (id.includes('leggings')) return '👖';
  if (id.includes('boots')) return '🥾';
  if (id.includes('shield')) return '🛡️';
  if (id.includes('elytra')) return '🪽';
  if (id.includes('totem')) return '🗿';
  if (id.includes('diamond')) return '💎';
  if (id.includes('emerald')) return '🟢';
  if (id.includes('gold') || id.includes('golden')) return '🪙';
  if (id.includes('iron')) return '⚙️';
  if (id.includes('netherite')) return '⚫';
  if (id.includes('bread') || id.includes('beef') || id.includes('pork') || id.includes('mutton') || id.includes('chicken') || id.includes('pie') || id.includes('carrot') || id.includes('apple') || id.includes('melon')) return '🍖';
  if (id.includes('bucket')) return '🪣';
  if (id.includes('potion')) return '🧪';
  if (id.includes('book')) return '📖';
  if (id.includes('firework') || id.includes('rocket')) return '🚀';
  if (id.includes('shulker_box') || id.includes('chest') || id.includes('barrel')) return '📦';
  if (id.includes('log') || id.includes('wood') || id.includes('plank')) return '🪵';
  if (id.includes('stone') || id.includes('cobble') || id.includes('deepslate')) return '🪨';
  if (id.includes('dirt') || id.includes('grass')) return '🌱';
  if (id.includes('arrow')) return '🎯';
  return '📦';
}

function renderSlotItem(slotEl, item, placeholderIcon) {
  if (!slotEl) return;
  slotEl.innerHTML = '';

  if (!item) {
    if (placeholderIcon) {
      slotEl.innerHTML = `<span class="slot-placeholder">${placeholderIcon}</span>`;
    }
    slotEl.onmouseenter = null;
    slotEl.onmouseleave = null;
    slotEl.onmousemove = null;
    return;
  }

  const iconEmoji = getItemIconEmoji(item.id);
  const countHtml = item.count > 1 ? `<span class="slot-count-badge">${item.count}</span>` : '';
  
  let durabilityHtml = '';
  if (item.damage && item.damage > 0) {
    durabilityHtml = `
      <div class="slot-durability-bar">
        <div class="slot-durability-fill" style="width: 70%;"></div>
      </div>
    `;
  }

  slotEl.innerHTML = `
    <span class="slot-item-icon">${iconEmoji}</span>
    ${countHtml}
    ${durabilityHtml}
  `;

  slotEl.onmouseenter = (e) => showItemTooltip(e, item);
  slotEl.onmouseleave = hideItemTooltip;
  slotEl.onmousemove = moveItemTooltip;
}

function showItemTooltip(e, item) {
  const tt = document.getElementById('mc-tooltip');
  if (!tt || !item) return;

  document.getElementById('mc-tt-name').textContent = item.name || item.id;
  
  const customEl = document.getElementById('mc-tt-custom');
  if (item.custom_name) {
    customEl.style.display = 'block';
    customEl.textContent = `"${item.custom_name}"`;
  } else {
    customEl.style.display = 'none';
  }

  const enchEl = document.getElementById('mc-tt-ench');
  if (item.enchantments && item.enchantments.length > 0) {
    enchEl.innerHTML = item.enchantments.map(ench => `<div>✨ ${ench.name} ${formatRoman(ench.level)}</div>`).join('');
  } else {
    enchEl.innerHTML = '';
  }

  const durEl = document.getElementById('mc-tt-durability');
  if (item.damage > 0) {
    durEl.style.display = 'block';
    durEl.textContent = `Durability lost: ${item.damage}`;
  } else {
    durEl.style.display = 'none';
  }

  tt.style.display = 'block';
  moveItemTooltip(e);
}

function moveItemTooltip(e) {
  const tt = document.getElementById('mc-tooltip');
  if (!tt) return;
  const x = e.clientX + 14;
  const y = e.clientY + 14;
  tt.style.left = `${x}px`;
  tt.style.top = `${y}px`;
}

function hideItemTooltip() {
  const tt = document.getElementById('mc-tooltip');
  if (tt) tt.style.display = 'none';
}

function formatRoman(num) {
  if (num === 1) return 'I';
  if (num === 2) return 'II';
  if (num === 3) return 'III';
  if (num === 4) return 'IV';
  if (num === 5) return 'V';
  return num;
}

// Toast Notifications
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// ==========================================================================
// MINECRAFT LIVE WEB MAP & PLAYER RADAR ENGINE
// ==========================================================================
let mapCanvas = null;
let mapCtx = null;
let mapData = {
  players: [],
  tiles: [],
  regions: [],
  waypoints: [],
  spawn: { x: 0, z: 0, dimension: 'overworld' }
};
let mapState = {
  currentDim: 'overworld',
  centerX: 0,
  centerZ: 0,
  zoom: 0.18, // pixels per block
  maxLayerY: 320, // height slicer max Y level
  showGrid: true,
  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  lastCenterX: 0,
  lastCenterZ: 0,
  cachedTileCanvases: {},
  regionImageCache: {},
  pendingRegionLoads: new Set(),
  playerHeadImages: {},
  initialized: false
};

const MC_MAP_PALETTE = [
  [0, 0, 0, 0], [127, 178, 56, 255], [247, 233, 163, 255], [199, 199, 199, 255],
  [255, 0, 0, 255], [160, 160, 255, 255], [167, 167, 167, 255], [0, 124, 0, 255],
  [255, 255, 255, 255], [164, 168, 184, 255], [151, 109, 77, 255], [112, 112, 112, 255],
  [64, 64, 255, 255], [143, 119, 72, 255], [255, 252, 245, 255], [216, 127, 51, 255],
  [178, 76, 216, 255], [102, 137, 211, 255], [229, 229, 51, 255], [127, 204, 25, 255],
  [242, 127, 165, 255], [76, 76, 76, 255], [153, 153, 153, 255], [76, 127, 153, 255],
  [127, 63, 178, 255], [51, 76, 178, 255], [102, 76, 51, 255], [102, 127, 51, 255],
  [153, 51, 51, 255], [25, 25, 25, 255], [250, 238, 77, 255], [92, 219, 213, 255],
  [74, 128, 255, 255], [0, 217, 58, 255], [129, 86, 49, 255], [112, 2, 0, 255]
];
const MC_SHADE_RATIOS = [180 / 255, 220 / 255, 1.0, 135 / 255];

function handleMapLayerChange(val) {
  const y = parseInt(val, 10);
  mapState.maxLayerY = y;
  
  const displayEl = document.getElementById('map-layer-display');
  if (displayEl) {
    let desc = 'Surface & Sky';
    if (y >= 320) desc = 'All / Surface';
    else if (y >= 120) desc = 'High Altitude';
    else if (y >= 64) desc = 'Sea Level & Hills';
    else if (y >= 16) desc = 'Upper Caves & Mines';
    else if (y >= -32) desc = 'Deep Caves';
    else desc = 'Deepslate & Bedrock';
    displayEl.textContent = `Y ≤ ${y} (${desc})`;
  }

  // Update preset button active state
  document.querySelectorAll('.btn-preset').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.getAttribute('data-y'), 10) === y);
  });

  drawMap();
  renderMapSidebar();
}

function setMapLayerPreset(y) {
  const slider = document.getElementById('map-layer-slider');
  if (slider) slider.value = y;
  handleMapLayerChange(y);
}

function initMap() {
  mapCanvas = document.getElementById('minecraft-map-canvas');
  if (!mapCanvas) return;
  mapCtx = mapCanvas.getContext('2d');

  const wrapper = document.getElementById('map-viewport');
  if (wrapper) {
    mapCanvas.width = wrapper.clientWidth || 800;
    mapCanvas.height = wrapper.clientHeight || 600;
  }

  if (mapState.initialized) {
    drawMap();
    return;
  }
  mapState.initialized = true;

  window.addEventListener('resize', () => {
    if (mapCanvas && wrapper) {
      mapCanvas.width = wrapper.clientWidth || 800;
      mapCanvas.height = wrapper.clientHeight || 600;
      drawMap();
    }
  });

  // Mouse Drag / Pan
  wrapper.addEventListener('mousedown', (e) => {
    mapState.isDragging = true;
    mapState.dragStartX = e.clientX;
    mapState.dragStartY = e.clientY;
    mapState.lastCenterX = mapState.centerX;
    mapState.lastCenterZ = mapState.centerZ;
    wrapper.style.cursor = 'grabbing';
  });

  window.addEventListener('mousemove', (e) => {
    if (mapState.isDragging) {
      const dx = e.clientX - mapState.dragStartX;
      const dy = e.clientY - mapState.dragStartY;
      mapState.centerX = mapState.lastCenterX - (dx / mapState.zoom);
      mapState.centerZ = mapState.lastCenterZ - (dy / mapState.zoom);
      drawMap();
    }

    // Update cursor HUD
    if (wrapper) {
      const rect = mapCanvas.getBoundingClientRect();
      if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom) {
        const mouseCanvasX = e.clientX - rect.left;
        const mouseCanvasY = e.clientY - rect.top;
        const worldX = Math.round(mapState.centerX + (mouseCanvasX - mapCanvas.width / 2) / mapState.zoom);
        const worldZ = Math.round(mapState.centerZ + (mouseCanvasY - mapCanvas.height / 2) / mapState.zoom);
        const coordsEl = document.getElementById('map-cursor-coords');
        if (coordsEl) coordsEl.textContent = `📍 X: ${worldX}, Z: ${worldZ} (Layer Y ≤ ${mapState.maxLayerY})`;
      }
    }
  });

  window.addEventListener('mouseup', () => {
    mapState.isDragging = false;
    if (wrapper) wrapper.style.cursor = 'crosshair';
  });

  // Mouse Wheel Zoom
  wrapper.addEventListener('wheel', (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.2 : 0.83;
    zoomMap(zoomFactor);
  }, { passive: false });
}

async function loadMapData() {
  try {
    const res = await fetch('/api/map/data');
    const data = await res.json();
    mapData.players = data.players || [];
    mapData.tiles = data.tiles || [];
    mapData.waypoints = data.waypoints || [];
    mapData.spawn = data.spawn || { x: 0, z: 0, dimension: 'overworld' };

    // Fetch Dimension Region Bounds (.mca)
    try {
      const regRes = await fetch(`/api/map/regions?dim=${encodeURIComponent(mapState.currentDim)}`);
      const regData = await regRes.json();
      mapData.regions = regData.regions || [];
    if (Object.keys(mapState.regionImageCache).length > 200) mapState.regionImageCache = {};
    } catch (re) {
      console.warn('Could not load regions:', re);
    }

    // Cache In-Game Map Tile Canvases
    if (data.tiles) {
      data.tiles.forEach(tile => {
        if (!mapState.cachedTileCanvases[tile.id]) {
          const offCanvas = document.createElement('canvas');
          offCanvas.width = 128;
          offCanvas.height = 128;
          const offCtx = offCanvas.getContext('2d');
          const imgData = offCtx.createImageData(128, 128);
          const raw = imgData.data;

          for (let i = 0; i < 16384; i++) {
            const b = tile.colors[i] & 0xFF;
            const baseId = b >> 2;
            const shadeId = b & 3;
            const mult = MC_SHADE_RATIOS[shadeId] || 1.0;
            const baseCol = MC_MAP_PALETTE[baseId] || [100, 100, 100, 255];

            const pIdx = i * 4;
            if (baseId === 0) {
              raw[pIdx + 3] = 0;
            } else {
              raw[pIdx] = Math.round(baseCol[0] * mult);
              raw[pIdx + 1] = Math.round(baseCol[1] * mult);
              raw[pIdx + 2] = Math.round(baseCol[2] * mult);
              raw[pIdx + 3] = baseCol[3];
            }
          }
          offCtx.putImageData(imgData, 0, 0);
          mapState.cachedTileCanvases[tile.id] = offCanvas;
        }
      });
    }

    renderMapSidebar();
    drawMap();
  } catch (e) {
    console.error('Failed to load map data:', e);
  }
}

async function setMapDimension(dim) {
  mapState.currentDim = dim;
  document.querySelectorAll('.map-dim-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-dim') === dim);
  });
  
  try {
    const regRes = await fetch(`/api/map/regions?dim=${encodeURIComponent(dim)}`);
    const regData = await regRes.json();
    mapData.regions = regData.regions || [];
    if (Object.keys(mapState.regionImageCache).length > 200) mapState.regionImageCache = {};
  } catch (re) {
    console.warn('Could not load regions for dimension:', re);
  }

  renderMapSidebar();
  drawMap();
}

function zoomMap(factor) {
  mapState.zoom = Math.max(0.02, Math.min(3.0, mapState.zoom * factor));
  const zoomLevelEl = document.getElementById('map-zoom-level');
  if (zoomLevelEl) zoomLevelEl.textContent = `Zoom: ${mapState.zoom.toFixed(2)}x`;
  drawMap();
}

function resetMapCenter() {
  mapState.centerX = mapData.spawn ? mapData.spawn.x : 0;
  mapState.centerZ = mapData.spawn ? mapData.spawn.z : 0;
  mapState.zoom = 0.18;
  drawMap();
}

function focusMapLocation(x, z) {
  mapState.centerX = x;
  mapState.centerZ = z;
  mapState.zoom = Math.max(mapState.zoom, 0.25);
  drawMap();
}

function toggleMapGrid() {
  mapState.showGrid = !mapState.showGrid;
  const btn = document.getElementById('btn-toggle-grid');
  if (btn) btn.style.background = mapState.showGrid ? 'var(--indigo)' : 'rgba(15, 23, 42, 0.6)';
  drawMap();
}

function drawMap() {
  if (!mapCanvas || !mapCtx) return;
  const w = mapCanvas.width;
  const h = mapCanvas.height;
  const ctx = mapCtx;

  // 1. Background Fill by Dimension & Subterranean Tint
  if (mapState.currentDim === 'the_nether') {
    ctx.fillStyle = '#1c0505';
  } else if (mapState.currentDim === 'the_end') {
    ctx.fillStyle = '#0b0414';
  } else {
    // Overworld - becomes deeper navy / dark cave tint when layer is reduced underground
    if (mapState.maxLayerY < 64) {
      ctx.fillStyle = '#050505';
    } else {
      ctx.fillStyle = '#050505';
    }
  }
  ctx.fillRect(0, 0, w, h);

  // Helper coordinate conversions
  const worldToCanvasX = (wx) => w / 2 + (wx - mapState.centerX) * mapState.zoom;
  const worldToCanvasY = (wz) => h / 2 + (wz - mapState.centerZ) * mapState.zoom;

  // Viewport world bounds
  const minWorldX = mapState.centerX - (w / 2) / mapState.zoom;
  const maxX = mapState.centerX + (w / 2) / mapState.zoom;
  const minWorldZ = mapState.centerZ - (h / 2) / mapState.zoom;
  const maxZ = mapState.centerZ + (h / 2) / mapState.zoom;

  // 2. Render Real World Region Tiles (.mca Anvil terrain)
  if (mapData.regions && mapData.regions.length > 0) {
    mapData.regions.forEach(r => {
      // Check if region intersects current viewport
      if (r.maxX >= minWorldX && r.minX <= maxX && r.maxZ >= minWorldZ && r.minZ <= maxZ) {
        const tileKey = `${mapState.currentDim}_${r.rx}_${r.rz}_${mapState.maxLayerY}_${r.revision}`;
        const cachedImg = mapState.regionImageCache[tileKey];

        const canvasX = worldToCanvasX(r.minX);
        const canvasY = worldToCanvasY(r.minZ);
        const renderSize = 512 * mapState.zoom;

        if (cachedImg && cachedImg.complete && cachedImg.naturalWidth > 0) {
          ctx.save();
          ctx.imageSmoothingEnabled = false;
          ctx.drawImage(cachedImg, canvasX, canvasY, renderSize, renderSize);
          ctx.restore();
        } else if (!mapState.pendingRegionLoads.has(tileKey)) {
          mapState.pendingRegionLoads.add(tileKey);
          const img = new Image();
          img.src = `/api/map/region_tile?dim=${encodeURIComponent(mapState.currentDim)}&rx=${r.rx}&rz=${r.rz}&layer=${mapState.maxLayerY}&revision=${r.revision}`;
          img.onload = () => {
            mapState.regionImageCache[tileKey] = img;
            mapState.pendingRegionLoads.delete(tileKey);
            drawMap();
          };
          img.onerror = () => {
            mapState.pendingRegionLoads.delete(tileKey);
          };
        }
      }
    });
  }

  // 3. Coordinate Grid Lines
  if (mapState.showGrid) {
    let gridStep = 500;
    if (mapState.zoom > 0.6) gridStep = 100;
    else if (mapState.zoom < 0.08) gridStep = 2000;

    const startGridX = Math.floor(minWorldX / gridStep) * gridStep;
    const startGridZ = Math.floor(minWorldZ / gridStep) * gridStep;

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    ctx.font = '10px JetBrains Mono';
    ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';

    for (let gx = startGridX; gx <= maxX; gx += gridStep) {
      const cx = worldToCanvasX(gx);
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, h);
      ctx.stroke();
      ctx.fillText(`${gx}`, cx + 4, 14);
    }

    for (let gz = startGridZ; gz <= maxZ; gz += gridStep) {
      const cy = worldToCanvasY(gz);
      ctx.beginPath();
      ctx.moveTo(0, cy);
      ctx.lineTo(w, cy);
      ctx.stroke();
      ctx.fillText(`${gz}`, 6, cy - 4);
    }

    // Origin 0,0 axis crosshair
    const origX = worldToCanvasX(0);
    const origZ = worldToCanvasY(0);
    ctx.strokeStyle = 'rgba(99, 102, 241, 0.3)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(origX, 0);
    ctx.lineTo(origX, h);
    ctx.moveTo(0, origZ);
    ctx.lineTo(w, origZ);
    ctx.stroke();
  }

  // 4. Render In-Game Explored Map Items Overlay (if available)
  if (mapData.tiles) {
    mapData.tiles.forEach(tile => {
      if (tile.dimension.toLowerCase().includes(mapState.currentDim.toLowerCase()) || 
         (mapState.currentDim === 'overworld' && !tile.dimension.includes('nether') && !tile.dimension.includes('end'))) {
        const offCanvas = mapState.cachedTileCanvases[tile.id];
        if (offCanvas) {
          const halfWidth = tile.widthBlocks / 2;
          const leftWX = tile.xCenter - halfWidth;
          const topWZ = tile.zCenter - halfWidth;
          const canvasX = worldToCanvasX(leftWX);
          const canvasY = worldToCanvasY(topWZ);
          const renderSize = tile.widthBlocks * mapState.zoom;

          ctx.save();
          if (mapState.maxLayerY < 64) {
            const dimFactor = Math.max(0.18, (mapState.maxLayerY + 64) / 128);
            ctx.globalAlpha = dimFactor;
          } else {
            ctx.globalAlpha = 1.0;
          }

          ctx.imageSmoothingEnabled = false;
          ctx.drawImage(offCanvas, canvasX, canvasY, renderSize, renderSize);

          ctx.strokeStyle = mapState.maxLayerY < 64 ? 'rgba(99, 102, 241, 0.12)' : 'rgba(99, 102, 241, 0.25)';
          ctx.lineWidth = 1;
          ctx.strokeRect(canvasX, canvasY, renderSize, renderSize);
          ctx.restore();
        }
      }
    });
  }

  // 4. World Spawn Marker
  if (mapState.currentDim === 'overworld') {
    const spawnCX = worldToCanvasX(0);
    const spawnCY = worldToCanvasY(0);
    ctx.save();
    if (mapState.maxLayerY < 64) {
      ctx.globalAlpha = 0.4;
    }
    ctx.font = '20px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('🏠', spawnCX, spawnCY + 7);
    ctx.font = '10px Inter, sans-serif';
    ctx.fillStyle = '#ffcc00';
    ctx.fillText('World Spawn (0, 0)', spawnCX, spawnCY + 22);
    ctx.restore();
  }

  // 5. Saved Custom Waypoints (Filtered by Height Layer)
  if (mapData.waypoints) {
    mapData.waypoints.forEach(wp => {
      if (wp.dimension === mapState.currentDim || (mapState.currentDim === 'overworld' && !wp.dimension)) {
        const wpY = wp.y !== undefined ? wp.y : 64;
        const inLayer = wpY <= mapState.maxLayerY;

        ctx.save();
        if (!inLayer) {
          ctx.globalAlpha = 0.25;
        }

        const cx = worldToCanvasX(wp.x);
        const cy = worldToCanvasY(wp.z);

        ctx.beginPath();
        ctx.arc(cx, cy, 6, 0, Math.PI * 2);
        ctx.fillStyle = wp.color || '#ffcc00';
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(wp.icon || '📍', cx, cy - 10);

        ctx.font = 'bold 11px Inter, sans-serif';
        ctx.fillStyle = inLayer ? '#fff7d6' : '#b9a85d';
        ctx.fillText(inLayer ? wp.name : `${wp.name} (Y=${wpY})`, cx, cy + 18);
        ctx.restore();
      }
    });
  }

  // 6. Live / Recorded Player Markers (Filtered & Highlighted by Height Layer)
  if (mapData.players) {
    mapData.players.forEach(p => {
      const pDim = (p.dimension || 'overworld').toLowerCase();
      if (pDim.includes(mapState.currentDim.toLowerCase()) || 
         (mapState.currentDim === 'overworld' && !pDim.includes('nether') && !pDim.includes('end'))) {
        const px = worldToCanvasX(p.pos.x);
        const py = worldToCanvasY(p.pos.z);
        const pY = p.pos.y !== undefined ? p.pos.y : 64;
        const inLayer = pY <= mapState.maxLayerY;

        ctx.save();
        if (!inLayer) {
          ctx.globalAlpha = 0.3; // Ghosted above active layer
        }

        // Player Radar Circle Pulse
        ctx.beginPath();
        ctx.arc(px, py, inLayer ? 15 : 11, 0, Math.PI * 2);
        ctx.fillStyle = p.is_online ? 'rgba(34, 197, 94, 0.25)' : 'rgba(99, 102, 241, 0.2)';
        ctx.fill();
        ctx.strokeStyle = p.is_online ? '#22c55e' : '#ffcc00';
        ctx.lineWidth = inLayer ? 2 : 1;
        ctx.stroke();

        // Player Avatar / Head Icon
        if (!mapState.playerHeadImages[p.name]) {
          const img = new Image();
          img.src = `https://mc-heads.net/avatar/${encodeURIComponent(p.name)}/20`;
          img.onload = () => { mapState.playerHeadImages[p.name] = img; drawMap(); };
          img.onerror = () => {
            const fallback = new Image();
            fallback.src = `https://mc-heads.net/avatar/MHF_Steve/20`;
            fallback.onload = () => { mapState.playerHeadImages[p.name] = fallback; drawMap(); };
          };
        } else {
          ctx.drawImage(mapState.playerHeadImages[p.name], px - 10, py - 10, 20, 20);
        }

        // Name tag & Stats label
        ctx.font = 'bold 11px Inter, sans-serif';
        ctx.textAlign = 'center';
        
        // Name pill background
        const labelText = inLayer ? p.name : `${p.name} [↑ Y=${Math.round(pY)}]`;
        const textWidth = ctx.measureText(labelText).width;
        ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
        ctx.fillRect(px - textWidth / 2 - 6, py - 28, textWidth + 12, 16);
        ctx.strokeStyle = inLayer ? (p.is_online ? '#22c55e' : 'rgba(255, 255, 255, 0.2)') : 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        ctx.strokeRect(px - textWidth / 2 - 6, py - 28, textWidth + 12, 16);

        ctx.fillStyle = inLayer ? (p.is_online ? '#4ade80' : '#fff7d6') : '#b9a85d';
        ctx.fillText(labelText, px, py - 16);

        // Altitude & health badge
        ctx.font = '9px JetBrains Mono';
        ctx.fillStyle = inLayer ? '#b9a85d' : '#64748b';
        ctx.fillText(`Y=${p.pos.y} • ❤️${p.health}`, px, py + 22);

        ctx.restore();
      }
    });
  }
}

function renderMapSidebar() {
  const playerList = document.getElementById('map-player-list');
  const countBadge = document.getElementById('map-players-count');
  if (playerList) {
    playerList.innerHTML = '';
    const filteredPlayers = (mapData.players || []).filter(p => {
      const pDim = (p.dimension || 'overworld').toLowerCase();
      return pDim.includes(mapState.currentDim.toLowerCase()) || 
            (mapState.currentDim === 'overworld' && !pDim.includes('nether') && !pDim.includes('end'));
    });

    if (countBadge) countBadge.textContent = `${filteredPlayers.length} in ${mapState.currentDim}`;

    if (filteredPlayers.length > 0) {
      filteredPlayers.forEach(p => {
        const inLayer = (p.pos.y !== undefined ? p.pos.y : 64) <= mapState.maxLayerY;
        const diff = Math.round(p.pos.y - mapState.maxLayerY);

        const item = document.createElement('div');
        item.className = 'map-player-item';
        item.style.opacity = inLayer ? '1' : '0.6';
        item.innerHTML = `
          <div class="map-item-info">
            <img src="https://mc-heads.net/avatar/${encodeURIComponent(p.name)}/28" style="width: 28px; height: 28px; border-radius: 6px; image-rendering: pixelated; border: 1px solid var(--border);">
            <div>
              <div style="font-weight: 700; font-size: 0.85rem; color: #f8fafc; display:flex; align-items:center; gap: 6px; flex-wrap: wrap;">
                ${p.name}
                ${p.is_online ? '<span class="pulse-tag" style="font-size:0.65rem; padding: 1px 5px;"><span class="dot-green"></span> Online</span>' : ''}
                ${inLayer ? '<span class="pulse-tag" style="font-size:0.65rem; padding: 1px 5px; background:rgba(56,189,248,0.15); color:#38bdf8; border-color:rgba(56,189,248,0.3);"><span class="dot-green" style="background:#38bdf8; box-shadow:0 0 6px #38bdf8;"></span> In Layer</span>' : `<span class="badge-type" style="font-size:0.65rem; background:rgba(255,255,255,0.06); padding:1px 5px;">↑ +${diff} Above</span>`}
              </div>
              <div class="map-item-coords">X: ${p.pos.x}, Y: ${p.pos.y}, Z: ${p.pos.z}</div>
            </div>
          </div>
          <div class="map-item-actions">
            <button class="btn-player-action" onclick="focusMapLocation(${p.pos.x}, ${p.pos.z})" title="Center Map">🎯 Jump</button>
            <button class="btn-player-action" style="background:var(--indigo); color:white; border-color:var(--indigo);" onclick="openInventoryModal('${p.uuid}', '${p.name}')" title="View Inventory">🎒</button>
          </div>
        `;
        playerList.appendChild(item);
      });
    } else {
      playerList.innerHTML = `<div class="empty-msg">No players currently in ${mapState.currentDim}.</div>`;
    }
  }

  const wpList = document.getElementById('map-waypoints-list');
  if (wpList) {
    wpList.innerHTML = '';
    const filteredWps = (mapData.waypoints || []).filter(w => {
      return w.dimension === mapState.currentDim || (mapState.currentDim === 'overworld' && !w.dimension);
    });

    if (filteredWps.length > 0) {
      filteredWps.forEach(w => {
        const wpY = w.y !== undefined ? w.y : 64;
        const inLayer = wpY <= mapState.maxLayerY;

        const item = document.createElement('div');
        item.className = 'map-wp-item';
        item.style.opacity = inLayer ? '1' : '0.6';
        item.innerHTML = `
          <div class="map-item-info">
            <span style="font-size: 1.2rem;">${w.icon || '📍'}</span>
            <div>
              <div style="font-weight: 700; font-size: 0.84rem; color: #f8fafc; display: flex; align-items: center; gap: 6px;">
                ${w.name}
                ${inLayer ? '<span style="font-size:0.65rem; color:#38bdf8; font-weight:700;">(In Layer)</span>' : `<span style="font-size:0.65rem; color:var(--text-dim);">(Y=${wpY})</span>`}
              </div>
              <div class="map-item-coords">X: ${w.x}, Y: ${wpY}, Z: ${w.z}</div>
            </div>
          </div>
          <div class="map-item-actions">
            <button class="btn-player-action" onclick="focusMapLocation(${w.x}, ${w.z})" title="Center Map">🎯 Jump</button>
            ${w.id !== 'spawn' ? `<button class="btn-player-action danger" onclick="deleteCustomWaypoint('${w.id}')" title="Delete Waypoint">🗑️</button>` : ''}
          </div>
        `;
        wpList.appendChild(item);
      });
    } else {
      wpList.innerHTML = '<div class="empty-msg">No custom waypoints in this dimension.</div>';
    }
  }
}

function showAddWaypointModal() {
  const modal = document.getElementById('waypoint-modal');
  if (!modal) return;
  document.getElementById('wp-input-dim').value = mapState.currentDim;
  document.getElementById('wp-input-x').value = Math.round(mapState.centerX);
  document.getElementById('wp-input-z').value = Math.round(mapState.centerZ);
  modal.style.display = 'flex';
}

function closeWaypointModal(event) {
  const modal = document.getElementById('waypoint-modal');
  if (modal) modal.style.display = 'none';
}

async function saveCustomWaypoint(e) {
  e.preventDefault();
  const name = document.getElementById('wp-input-name').value.trim();
  const dim = document.getElementById('wp-input-dim').value;
  const x = parseFloat(document.getElementById('wp-input-x').value);
  const y = parseFloat(document.getElementById('wp-input-y').value) || 64;
  const z = parseFloat(document.getElementById('wp-input-z').value);
  const icon = document.getElementById('wp-input-icon').value;
  const color = document.getElementById('wp-input-color').value;

  if (!name) return;

  try {
    const res = await fetch('/api/map/waypoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, dimension: dim, x, y, z, icon, color })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Waypoint "${name}" added!`, 'success');
      closeWaypointModal();
      loadMapData();
    } else {
      showToast(data.error || 'Failed to save waypoint', 'error');
    }
  } catch (e) {
    showToast('Failed to save waypoint', 'error');
  }
}

async function deleteCustomWaypoint(id) {
  if (!confirm('Are you sure you want to delete this waypoint?')) return;
  try {
    const res = await fetch(`/api/map/waypoints?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast('Waypoint deleted', 'info');
      loadMapData();
    }
  } catch (e) {
    showToast('Failed to delete waypoint', 'error');
  }
}
