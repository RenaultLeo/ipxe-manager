(function () {
  var charts = {};
  var lastSnapshot = null;
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

  function renderSnapshot(snap) {
    if (!snap) return;
    lastSnapshot = snap;
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
    renderCharts(snap);
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
    setLoading(true);
    var url = "/admin/supervision/api/snapshot";
    if (full) url += "?full=1";
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
      })
      .catch(function () {})
      .finally(function () {
        setLoading(false);
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
    }, 90000);
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
