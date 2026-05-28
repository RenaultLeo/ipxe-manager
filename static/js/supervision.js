(function () {
  var charts = {};
  var lastSnapshot = null;
  var networkHistory = [];
  var networkLastPoint = null;
  var NETWORK_HISTORY_LIMIT = 120;
  var lastHeavyChartsRenderTs = 0;
  var HEAVY_CHARTS_MIN_INTERVAL_MS = 45000;
  var i18n = {
    active: "active",
    openPorts: "open ports",
    sudoOk: "sudo systemctl OK",
    sudoNo: "sudo systemctl not configured",
  };
  var i18nNode = document.getElementById("supervision-i18n");
  if (i18nNode) {
    try {
      var parsed = JSON.parse(i18nNode.textContent || "{}");
      if (parsed.active) i18n.active = parsed.active;
      if (parsed.openPorts) i18n.openPorts = parsed.openPorts;
      if (parsed.sudoOk) i18n.sudoOk = parsed.sudoOk;
      if (parsed.sudoNo) i18n.sudoNo = parsed.sudoNo;
    } catch (e) { /* keep defaults */ }
  }

  function el(id) {
    return document.getElementById(id);
  }

  function parseSnapshot() {
    var node = el("initial-snapshot");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "{}");
    } catch (e) {
      return null;
    }
  }

  function statusIcon(st) {
    if (st === "ok") return '<i class="bi bi-check-circle text-success"></i>';
    if (st === "warn") return '<i class="bi bi-exclamation-triangle text-warning"></i>';
    if (st === "error") return '<i class="bi bi-x-circle text-danger"></i>';
    return '<i class="bi bi-question-circle text-muted"></i>';
  }

  function statusLed(online, status) {
    var cls = "status-led-unknown";
    if (online === true || status === "ok" || status === "active") cls = "status-led-on";
    else if (online === false || status === "error" || status === "inactive") cls = "status-led-off";
    else if (status === "warn") cls = "status-led-warn";
    return '<span class="status-led ' + cls + '"></span>';
  }

  function renderStatusTable(tableId, rows, nameKey) {
    var tbody = document.querySelector(tableId + " tbody");
    if (!tbody) return;
    tbody.innerHTML = rows
      .map(function (row) {
        var name = row[nameKey] || row.name || row.unit || row.label || "—";
        var ledStatus =
          row.status ||
          (row.active === true ? "active" : row.active === false ? "inactive" : null);
        return (
          "<tr><td class=\"text-center\">" +
          statusLed(row.online, ledStatus) +
          "</td><td>" +
          name +
          "</td></tr>"
        );
      })
      .join("");
  }

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
  }

  function parseIsoDate(value) {
    if (!value) return null;
    var ts = Date.parse(value);
    return Number.isFinite(ts) ? ts : null;
  }

  function formatRate(bytesPerSec) {
    if (bytesPerSec == null || !Number.isFinite(bytesPerSec)) return "—";
    var b = Math.max(0, bytesPerSec);
    var units = ["B/s", "KB/s", "MB/s", "GB/s"];
    var idx = 0;
    while (b >= 1024 && idx < units.length - 1) {
      b /= 1024;
      idx += 1;
    }
    var decimals;
    if (idx === 0) {
      // Keep precision on very small incoming traffic.
      decimals = b >= 100 ? 0 : b >= 10 ? 1 : 2;
    } else {
      decimals = b >= 100 ? 0 : b >= 10 ? 1 : 2;
    }
    return b.toFixed(decimals) + " " + units[idx];
  }

  function formatBytes(bytes) {
    if (bytes == null || !Number.isFinite(bytes)) return "—";
    var b = Math.max(0, bytes);
    var units = ["B", "KB", "MB", "GB", "TB"];
    var idx = 0;
    while (b >= 1024 && idx < units.length - 1) {
      b /= 1024;
      idx += 1;
    }
    var decimals = b >= 100 || idx === 0 ? 0 : b >= 10 ? 1 : 2;
    return b.toFixed(decimals) + " " + units[idx];
  }

  function formatTimeLabel(ts) {
    var dt = new Date(ts);
    var h = String(dt.getHours()).padStart(2, "0");
    var m = String(dt.getMinutes()).padStart(2, "0");
    var s = String(dt.getSeconds()).padStart(2, "0");
    return h + ":" + m + ":" + s;
  }

  function computeNetworkTotals(host) {
    var ifaces = (host && host.network) || [];
    var rxBytes = 0;
    var txBytes = 0;
    ifaces.forEach(function (n) {
      if (!n || n.iface === "lo") return;
      var rxRaw = Number(n.rx_bytes);
      var txRaw = Number(n.tx_bytes);
      if (Number.isFinite(rxRaw)) {
        rxBytes += rxRaw;
      } else {
        var rxMb = Number(n.rx_mb);
        if (Number.isFinite(rxMb)) rxBytes += rxMb * 1024 * 1024;
      }
      if (Number.isFinite(txRaw)) {
        txBytes += txRaw;
      } else {
        var txMb = Number(n.tx_mb);
        if (Number.isFinite(txMb)) txBytes += txMb * 1024 * 1024;
      }
    });
    return { rxBytes: rxBytes, txBytes: txBytes, ifaceCount: ifaces.length };
  }

  function pushNetworkPoint(snap) {
    if (!snap || !snap.host) return;
    var totals = computeNetworkTotals(snap.host);
    var ts = parseIsoDate(snap.generated_at);
    if (!ts) ts = Date.now();
    var rateRx = null;
    var rateTx = null;
    if (networkLastPoint && ts > networkLastPoint.ts) {
      var dtSec = (ts - networkLastPoint.ts) / 1000;
      if (dtSec > 0) {
        rateRx = (totals.rxBytes - networkLastPoint.rxBytes) / dtSec;
        rateTx = (totals.txBytes - networkLastPoint.txBytes) / dtSec;
      }
    }
    if (!Number.isFinite(rateRx) || rateRx < 0) rateRx = 0;
    if (!Number.isFinite(rateTx) || rateTx < 0) rateTx = 0;
    if (networkLastPoint && ts <= networkLastPoint.ts) {
      return;
    }
    var point = {
      ts: ts,
      label: formatTimeLabel(ts),
      rxBytes: totals.rxBytes,
      txBytes: totals.txBytes,
      rateRx: rateRx,
      rateTx: rateTx,
      ifaceCount: totals.ifaceCount,
    };
    networkHistory.push(point);
    if (networkHistory.length > NETWORK_HISTORY_LIMIT) {
      networkHistory = networkHistory.slice(networkHistory.length - NETWORK_HISTORY_LIMIT);
    }
    networkLastPoint = {
      ts: ts,
      rxBytes: totals.rxBytes,
      txBytes: totals.txBytes,
    };
  }

  function setNetworkHistory(points) {
    if (!Array.isArray(points) || !points.length) return;
    var mapped = [];
    points.forEach(function (p) {
      if (!p) return;
      var ts = Number(p.ts);
      if (!Number.isFinite(ts)) {
        ts = parseIsoDate(p.at) || Date.now();
      } else if (ts < 1e12) {
        ts = Math.round(ts * 1000);
      }
      var rxRate = Number(p.rx_rate_bps);
      var txRate = Number(p.tx_rate_bps);
      mapped.push({
        ts: ts,
        label: formatTimeLabel(ts),
        rxBytes: Number(p.rx_bytes) || 0,
        txBytes: Number(p.tx_bytes) || 0,
        rateRx: Number.isFinite(rxRate) ? Math.max(0, rxRate) : 0,
        rateTx: Number.isFinite(txRate) ? Math.max(0, txRate) : 0,
        ifaceCount: Number(p.iface_count) || 0,
      });
    });
    if (!mapped.length) return;
    mapped.sort(function (a, b) { return a.ts - b.ts; });
    networkHistory = mapped.slice(-NETWORK_HISTORY_LIMIT);
    var last = networkHistory[networkHistory.length - 1];
    networkLastPoint = { ts: last.ts, rxBytes: last.rxBytes, txBytes: last.txBytes };
    renderNetworkStats();
    if (isHealthTabVisible()) renderNetworkChart();
  }

  function refreshNetworkHistory() {
    fetch("/admin/supervision/api/network-history?limit=" + NETWORK_HISTORY_LIMIT, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("fetch-network-history");
        return r.json();
      })
      .then(function (data) {
        if (data && Array.isArray(data.points)) {
          setNetworkHistory(data.points);
        }
      })
      .catch(function () {});
  }

  function renderNetworkStats() {
    if (!networkHistory.length) return;
    var last = networkHistory[networkHistory.length - 1];
    if (el("stat-net-rx")) el("stat-net-rx").textContent = formatRate(last.rateRx);
    if (el("stat-net-tx")) el("stat-net-tx").textContent = formatRate(last.rateTx);
    if (el("stat-net-total")) {
      el("stat-net-total").textContent = "↓ " + formatBytes(last.rxBytes) + " / ↑ " + formatBytes(last.txBytes);
    }
    if (el("stat-net-ifaces")) el("stat-net-ifaces").textContent = String(last.ifaceCount || 0);
    if (el("network-window-label")) {
      var first = networkHistory[0];
      var minutes = Math.max(1, Math.round((last.ts - first.ts) / 60000));
      el("network-window-label").textContent = "Fenêtre " + minutes + " min";
    }
  }

  function renderNetworkChart() {
    if (typeof Chart === "undefined") return;
    var canvas = el("chart-network-traffic");
    if (!canvas) return;
    if (!networkHistory.length) {
      destroyChart("networkTraffic");
      return;
    }
    var labels = networkHistory.map(function (p) { return p.label; });
    var rxData = networkHistory.map(function (p) { return p.rateRx; });
    var txData = networkHistory.map(function (p) { return p.rateTx; });
    if (!charts.networkTraffic) {
      charts.networkTraffic = new Chart(canvas, {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Entrant (B/s)",
              data: rxData,
              borderColor: "#38bdf8",
              backgroundColor: "rgba(56,189,248,0.15)",
              tension: 0.3,
              pointRadius: 0,
              fill: true,
            },
            {
              label: "Sortant (B/s)",
              data: txData,
              borderColor: "#a78bfa",
              backgroundColor: "rgba(167,139,250,0.12)",
              tension: 0.3,
              pointRadius: 0,
              fill: true,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { intersect: false, mode: "index" },
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  var label = ctx.dataset.label ? ctx.dataset.label + " : " : "";
                  return label + formatRate(ctx.parsed.y);
                },
              },
            },
          },
          scales: {
            y: {
              ticks: {
                callback: function (value) { return formatRate(Number(value)); },
              },
            },
          },
        },
      });
      return;
    }
    charts.networkTraffic.data.labels = labels;
    charts.networkTraffic.data.datasets[0].data = rxData;
    charts.networkTraffic.data.datasets[1].data = txData;
    charts.networkTraffic.update("none");
  }

  function renderSnapshot(snap) {
    if (!snap) return;
    lastSnapshot = snap;
    pushNetworkPoint(snap);
    renderNetworkStats();
    var ss = snap.services_summary || {};
    var ps = snap.ports_summary || {};
    var host = snap.host || {};
    if (el("stat-services")) {
      el("stat-services").textContent =
        (ss.active || 0) + "/" + (ss.total || 0) + " " + i18n.active;
    }
    if (el("stat-cpu")) el("stat-cpu").textContent = (host.cpu_percent != null ? host.cpu_percent + "%" : "—");
    if (el("stat-ram")) el("stat-ram").textContent = (host.memory_percent != null ? host.memory_percent + "%" : "—");
    if (el("stat-ports")) {
      el("stat-ports").textContent =
        (ps.open || 0) + "/" + (ps.total || 0) + " " + i18n.openPorts;
    }
    if (el("last-update")) el("last-update").textContent = snap.generated_at || "—";
    if (el("sudo-hint")) {
      el("sudo-hint").innerHTML = snap.can_sudo_systemctl
        ? '<span class="badge bg-success-subtle text-success">' + i18n.sudoOk + "</span>"
        : '<span class="badge bg-warning-subtle text-warning">' + i18n.sudoNo + "</span>";
    }

    renderStatusTable("#table-machines", snap.machines || [], "name");

    var hostHtml = "";
    if (host.platform) {
      hostHtml += '<div class="text-muted">' + host.platform + "</div>";
    }
    if (host.uptime_human || host.memory_used_gb != null) {
      hostHtml +=
        '<div class="mt-2">Uptime : ' +
        (host.uptime_human || "—") +
        "</div>";
      hostHtml +=
        '<div class="mt-1">RAM : ' +
        (host.memory_used_gb || "?") +
        " / " +
        (host.memory_total_gb || "?") +
        " Go</div>";
    }
    if (host.network && host.network.length) {
      hostHtml += '<div class="mt-2"><strong>Réseau</strong><ul class="mb-0 ps-3">';
      host.network.slice(0, 5).forEach(function (n) {
        hostHtml +=
          "<li><code>" +
          n.iface +
          "</code> " +
          (n.ips && n.ips.length ? n.ips.join(", ") : "") +
          ' <span class="text-muted">↓' +
          n.rx_mb +
          "M ↑" +
          n.tx_mb +
          "M</span></li>";
      });
      hostHtml += "</ul></div>";
    }
    if (el("host-details")) el("host-details").innerHTML = hostHtml;

    renderStatusTable(
      "#table-services",
      (snap.services || []).map(function (s) {
        return {
          unit: s.unit,
          online: s.active,
          status: s.active ? "ok" : "error",
        };
      }),
      "unit"
    );

    var tbodyPaths = document.querySelector("#table-paths tbody");
    if (tbodyPaths && snap.paths) {
      tbodyPaths.innerHTML = snap.paths
        .map(function (p) {
          return (
            "<tr><td class=\"text-center\">" +
            statusLed(p.status === "ok", p.status) +
            "</td><td>" +
            p.label +
            "</td><td class=\"font-monospace small text-muted\">" +
            p.path +
            "</td></tr>"
          );
        })
        .join("");
    }

    var checksEl = el("checks-list");
    if (checksEl && snap.checks) {
      checksEl.innerHTML = snap.checks
        .map(function (c) {
          return (
            '<div class="d-flex justify-content-between align-items-center py-1 border-bottom border-secondary">' +
            "<span>" +
            c.label +
            "</span><span>" +
            statusIcon(c.status) +
            ' <span class="text-muted small ms-1">' +
            (c.detail || "") +
            "</span></span></div>"
          );
        })
        .join("");
    }

    if (typeof Chart === "undefined" || !isHealthTabVisible()) return;
    var now = Date.now();
    if (!lastHeavyChartsRenderTs || now - lastHeavyChartsRenderTs >= HEAVY_CHARTS_MIN_INTERVAL_MS) {
      renderCharts(snap);
      lastHeavyChartsRenderTs = now;
    }
    renderNetworkChart();
  }

  function isHealthTabVisible() {
    var pane = el("tab-health");
    return !!(pane && pane.classList.contains("active"));
  }

  function isIntegrityHash(hash) {
    return hash === "#integrity" || hash === "#tab-integrity";
  }

  function syncSupervisionUrl(tab) {
    if (!window.history || !window.history.replaceState) return;
    if (window.location.pathname.indexOf("/admin/supervision") === -1) return;
    var path = window.location.pathname.replace(/\/$/, "") || "/admin/supervision";
    var url = tab === "integrity" ? path + "#integrity" : path;
    history.replaceState(null, "", url);
  }

  function tabTriggerFromEvent(ev) {
    if (!ev || !ev.target || !ev.target.closest) return null;
    return ev.target.closest("[data-bs-toggle='tab']");
  }

  function onHealthTabActivated() {
    syncSupervisionUrl("health");
    if (lastSnapshot) {
      window.requestAnimationFrame(function () {
        renderCharts(lastSnapshot);
        lastHeavyChartsRenderTs = Date.now();
        renderNetworkChart();
      });
    } else {
      resizeCharts();
    }
  }

  function onIntegrityTabActivated() {
    syncSupervisionUrl("integrity");
  }

  function resizeCharts() {
    Object.keys(charts).forEach(function (key) {
      if (charts[key]) {
        try {
          charts[key].resize();
        } catch (e) { /* ignore */ }
      }
    });
  }

  function renderCharts(snap) {
    if (typeof Chart === "undefined" || !snap) return;
    var ss = snap.services_summary || {};
    var host = snap.host || {};
    var inactive = ss.inactive || 0;
    var active = ss.active || 0;
    destroyChart("services");
    charts.services = new Chart(el("chart-services"), {
      type: "doughnut",
      data: {
        labels: ["Actifs", "Inactifs"],
        datasets: [
          {
            data: [active, inactive],
            backgroundColor: ["#22c55e", "#64748b"],
          },
        ],
      },
      options: { plugins: { legend: { position: "bottom" } } },
    });

    var ports = snap.ports || [];
    destroyChart("ports");
    charts.ports = new Chart(el("chart-ports"), {
      type: "bar",
      data: {
        labels: ports.map(function (p) {
          return p.port + "";
        }),
        datasets: [
          {
            label: "Ouvert",
            data: ports.map(function (p) {
              return p.open ? 1 : 0;
            }),
            backgroundColor: ports.map(function (p) {
              return p.open ? "#3b82f6" : "#334155";
            }),
          },
        ],
      },
      options: {
        scales: { y: { max: 1, ticks: { stepSize: 1 } } },
        plugins: { legend: { display: false } },
      },
    });

    destroyChart("resources");
    charts.resources = new Chart(el("chart-resources"), {
      type: "bar",
      data: {
        labels: ["CPU %", "RAM %"],
        datasets: [
          {
            data: [host.cpu_percent || 0, host.memory_percent || 0],
            backgroundColor: ["#f59e0b", "#8b5cf6"],
          },
        ],
      },
      options: {
        indexAxis: "y",
        scales: { x: { max: 100 } },
        plugins: { legend: { display: false } },
      },
    });

    var disks = (host.disk_partitions || []).slice(0, 6);
    destroyChart("disk");
    charts.disk = new Chart(el("chart-disk"), {
      type: "bar",
      data: {
        labels: disks.map(function (d) {
          return d.mount;
        }),
        datasets: [
          {
            label: "% utilisé",
            data: disks.map(function (d) {
              return d.percent;
            }),
            backgroundColor: "#06b6d4",
          },
        ],
      },
      options: {
        scales: { y: { max: 100 } },
        plugins: { legend: { display: false } },
      },
    });
  }

  function setLoading(show) {
    var box = el("supervision-loading");
    if (!box) return;
    box.classList.toggle("d-none", !show);
  }

  function refreshSnapshot(full) {
    var btn = el("btn-refresh-snapshot");
    if (btn) btn.disabled = true;
    if (full) setLoading(true);
    var url = "/admin/supervision/api/snapshot";
    if (full) {
      url += "?full=1";
    } else {
      url += "?force=1";
    }
    fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("fetch");
        return r.json();
      })
      .then(function (data) {
        renderSnapshot(data);
        refreshNetworkHistory();
      })
      .catch(function () {})
      .finally(function () {
        if (full) setLoading(false);
        if (btn) btn.disabled = false;
      });
  }

  var refreshBtn = el("btn-refresh-snapshot");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      refreshSnapshot(true);
    });
  }

  function bindSupervisionTabs() {
    var tabList = document.querySelector(".super-tabs");
    if (tabList) {
      tabList.addEventListener("shown.bs.tab", function (ev) {
        var trigger = tabTriggerFromEvent(ev);
        if (!trigger) return;
        var target = trigger.getAttribute("data-bs-target");
        if (target === "#tab-integrity") {
          onIntegrityTabActivated();
        } else if (target === "#tab-health") {
          onHealthTabActivated();
        }
      });
    }
    var btnHealth = el("tab-btn-health");
    var btnIntegrity = el("tab-btn-integrity");
    if (btnHealth) {
      btnHealth.addEventListener("click", function () {
        window.setTimeout(onHealthTabActivated, 0);
      });
    }
    if (btnIntegrity) {
      btnIntegrity.addEventListener("click", function () {
        window.setTimeout(onIntegrityTabActivated, 0);
      });
    }
  }

  var snapshotPollTimer = null;

  function scheduleSnapshotPoll() {
    if (snapshotPollTimer) {
      clearInterval(snapshotPollTimer);
      snapshotPollTimer = null;
    }
    if (document.hidden) return;
    snapshotPollTimer = setInterval(function () {
      if (!document.hidden) refreshSnapshot(false);
    }, 15000);
  }

  bindSupervisionTabs();

  if (isIntegrityHash(window.location.hash)) {
    var tabBtn = document.querySelector('[data-bs-target="#tab-integrity"]');
    if (tabBtn && window.bootstrap) {
      new bootstrap.Tab(tabBtn).show();
    }
    syncSupervisionUrl("integrity");
  } else {
    syncSupervisionUrl("health");
  }

  refreshSnapshot(false);
  refreshNetworkHistory();
  scheduleSnapshotPoll();
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (snapshotPollTimer) clearInterval(snapshotPollTimer);
      snapshotPollTimer = null;
    } else {
      refreshSnapshot(false);
      scheduleSnapshotPoll();
    }
  });
})();
