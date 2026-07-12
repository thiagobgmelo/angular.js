/* Dashboard: gráfico (TradingView Lightweight Charts) + sinais + carteira paper. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // escapa valores dinâmicos antes de interpolar em innerHTML (anti-XSS)
  const esc = (v) =>
    String(v).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  const fmt = (v, d) => {
    if (v == null) return "—";
    const n = Number(v);
    const digits = d ?? (Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 1 ? 4 : 6);
    return n.toLocaleString("pt-BR", { maximumFractionDigits: digits });
  };

  // ---------- gráfico ----------
  const chartEl = $("chart");
  const chart = LightweightCharts.createChart(chartEl, {
    layout: { background: { color: "#1a1a19" }, textColor: "#c3c2b7" },
    grid: {
      vertLines: { color: "#232322" },
      horzLines: { color: "#232322" },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#33332f" },
    timeScale: { borderColor: "#33332f", timeVisible: true },
    autoSize: true,
  });
  const candles = chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350",
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    borderVisible: false,
  });
  const emaFast = chart.addLineSeries({
    color: "#3987e5", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
  });
  const emaSlow = chart.addLineSeries({
    color: "#c98500", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
  });
  let priceLines = [];
  let contextLines = [];

  const mkLine = (bucket) => (price, title, color, style, axisLabel) => {
    bucket.push(
      candles.createPriceLine({
        price, title, color,
        lineWidth: 1,
        lineStyle: style ?? LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: axisLabel !== false,
      })
    );
  };

  function clearLines(bucket) {
    bucket.forEach((pl) => candles.removePriceLine(pl));
    bucket.length = 0;
  }

  function drawSignalLevels(signal) {
    clearLines(priceLines);
    if (!signal) return;
    const mk = mkLine(priceLines);
    mk(signal.entry, "entrada", "#c3c2b7", LightweightCharts.LineStyle.Solid);
    mk(signal.stop, "stop", "#e66767");
    signal.targets.forEach((t, i) => mk(t, "TP" + (i + 1), "#199e70"));
    if (signal.liquidation_price_est) {
      mk(signal.liquidation_price_est, "liq.est", "#8a3535");
    }
  }

  function drawContextOverlays(ctx) {
    // cenário da análise: zonas de S/R + níveis de Fibonacci + padrão de candle
    clearLines(contextLines);
    const mk = mkLine(contextLines);
    const zones = ctx.zones || [];
    const supports = zones.filter((z) => z.kind === "support")
      .sort((a, b) => b.price - a.price).slice(0, 3);
    const resistances = zones.filter((z) => z.kind === "resistance")
      .sort((a, b) => a.price - b.price).slice(0, 3);
    supports.forEach((z) => mk(z.price, `S (${z.touches}t)`, "#6f6e64",
      LightweightCharts.LineStyle.Dashed, false));
    resistances.forEach((z) => mk(z.price, `R (${z.touches}t)`, "#6f6e64",
      LightweightCharts.LineStyle.Dashed, false));
    const fib = ctx.fibonacci || {};
    for (const key of ["0.382", "0.500", "0.618"]) {
      if (fib[key] != null) {
        mk(fib[key], "Fib " + (parseFloat(key) * 100).toFixed(1) + "%",
          "#4a3aa7", LightweightCharts.LineStyle.Dotted, false);
      }
    }
    // marcador do padrão de candle no último candle fechado
    if (ctx.patterns && ctx.patterns.length && lastRows.length >= 2) {
      const t = lastRows[lastRows.length - 2].time;
      candles.setMarkers([{
        time: t,
        position: "belowBar",
        color: "#c98500",
        shape: "arrowUp",
        text: ctx.patterns.join(","),
      }]);
    } else {
      candles.setMarkers([]);
    }
  }

  // ---------- estado ----------
  let cfg = { pairs: ["BTC/USDT"], timeframes: ["4h"] };

  // token de acesso (deploy com API_TOKEN); guardado uma vez no localStorage
  const getToken = () => localStorage.getItem("api_token") || "";

  async function api(path, opts, retried) {
    opts = opts || {};
    const token = getToken();
    opts.headers = Object.assign({}, opts.headers,
      token ? { Authorization: "Bearer " + token } : {});
    const resp = await fetch(path, opts);
    if (resp.status === 401 && !retried) {
      const t = window.prompt("Token de acesso (API_TOKEN do servidor):");
      if (t) {
        localStorage.setItem("api_token", t.trim());
        return api(path, opts, true);
      }
    }
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const body = await resp.json();
        if (body && body.detail) detail = JSON.stringify(body.detail);
      } catch (_) { /* corpo não-JSON: mantém statusText */ }
      throw new Error(detail);
    }
    return resp.json();
  }

  function setPairOptions(pairs) {
    const pairSel = $("pair");
    const current = pairSel.value;
    pairSel.innerHTML = pairs.map((p) => `<option>${esc(p)}</option>`).join("");
    if (pairs.includes(current)) pairSel.value = current; // preserva a seleção
  }

  async function loadConfig() {
    cfg = await api("/api/config");
    setPairOptions(cfg.pairs);
    const tfSel = $("timeframe");
    tfSel.innerHTML = cfg.timeframes.map((t) => `<option>${esc(t)}</option>`).join("");
    tfSel.value = cfg.timeframes.includes("4h") ? "4h" : cfg.timeframes[0];
  }

  // último candle exibido (em formação) — atualizado ao vivo pelos ticks
  let lastCandle = null;
  let lastRows = [];
  let tfSeconds = 14400;
  const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                       "1h": 3600, "4h": 14400, "1d": 86400 };

  async function loadChart() {
    const symbol = $("pair").value, timeframe = $("timeframe").value;
    tfSeconds = TF_SECONDS[timeframe] || 14400;
    const rows = await api(
      `/api/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=300`
    );
    candles.setData(rows.map((r) => ({
      time: r.time, open: r.open, high: r.high, low: r.low, close: r.close,
    })));
    emaFast.setData(rows.filter((r) => r.ema_fast != null).map((r) => ({ time: r.time, value: r.ema_fast })));
    emaSlow.setData(rows.filter((r) => r.ema_slow != null).map((r) => ({ time: r.time, value: r.ema_slow })));
    lastRows = rows;
    lastCandle = rows.length ? {
      time: rows[rows.length - 1].time,
      open: rows[rows.length - 1].open, high: rows[rows.length - 1].high,
      low: rows[rows.length - 1].low, close: rows[rows.length - 1].close,
    } : null;
    chart.timeScale().fitContent();
  }

  function renderAnalysis(ctx) {
    const box = $("analysis-box");
    const structureLabel = { uptrend: "Alta", downtrend: "Baixa", range: "Lateral" }[ctx.structure] || ctx.structure;
    const head =
      `Preço: <b>${fmt(ctx.price)}</b> · Estrutura: ${esc(structureLabel)} · ` +
      `Suporte ${fmt(ctx.support)} · Resistência ${fmt(ctx.resistance)} · ` +
      `Confluência <b>L${ctx.long_score}</b>/S<b>${ctx.short_score}</b> de ${ctx.max_score}`;
    const rows = (ctx.criteria || []).map((c) =>
      `<tr><td>${esc(c.label)}</td>` +
      `<td class="${c.long ? "ok" : "no"}">${c.long ? "✓" : "—"}</td>` +
      `<td class="${c.short ? "ok" : "no"}">${c.short ? "✓" : "—"}</td>` +
      `<td>${esc(c.value)}</td></tr>`
    ).join("");
    box.innerHTML = `<div>${head}</div>` + (rows
      ? `<table class="crit"><thead><tr><th>Critério</th><th>Long</th><th>Short</th><th>Leitura</th></tr></thead><tbody>${rows}</tbody></table>`
      : "");
  }

  function signalHtml(s, withRationale) {
    const dirClass = s.direction === "long" ? "dir-long" : "dir-short";
    const dirLabel = s.direction === "long" ? "LONG ▲" : "SHORT ▼";
    const kind = s.trade_type === "swing" ? "Swing" : "Day trade";
    const rationale =
      withRationale && s.rationale && s.rationale.length
        ? `<ul class="rationale">${s.rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
        : "";
    const riskDist = Math.abs(s.entry - s.stop);
    const targetLines = s.targets.map((t, i) => {
      const rr = riskDist > 0 ? (Math.abs(t - s.entry) / riskDist).toFixed(1) : "?";
      const pct = ((t - s.entry) / s.entry) * 100;
      return `TP${i + 1} ${fmt(t)} <span class="muted">(${rr}R · ${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span>`;
    }).join("<br>");
    const stopPct = ((s.stop - s.entry) / s.entry) * 100;
    const lev = withRationale && s.suggested_leverage > 1
      ? `<div class="lev-box">Alavancagem sugerida: <b>${esc(s.suggested_leverage)}x</b> · ` +
        `margem ~${fmt(s.margin_required)} USDT · liq. est. ${fmt(s.liquidation_price_est)}<br>` +
        `<span class="muted">${esc(s.leverage_rationale || "")}</span></div>`
      : "";
    return `<div class="sig">
      <div class="head"><span class="${dirClass}">${dirLabel}</span>
        <span>${esc(s.symbol)} · ${esc(s.timeframe)} · ${kind} · ${esc(s.score)}/${esc(s.max_score)}</span></div>
      <div class="lvls">Entrada ${fmt(s.entry)} · Stop ${fmt(s.stop)} <span class="muted">(${stopPct.toFixed(1)}%)</span><br>
        ${targetLines}</div>
      ${lev}
      <div class="when">${esc(new Date(s.created_at).toLocaleString("pt-BR"))}</div>
      ${rationale}</div>`;
  }

  async function loadAnalysis() {
    const symbol = $("pair").value, timeframe = $("timeframe").value;
    const { signal, context } = await api(
      `/api/analysis?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`
    );
    renderAnalysis(context);
    drawSignalLevels(signal);
    drawContextOverlays(context);
    $("signal-body").innerHTML = signal
      ? signalHtml(signal, true)
      : '<span class="muted">Sem sinal — confluência insuficiente no momento.</span>';
  }

  function radarItemHtml(r) {
    const dirClass = r.direction === "long" ? "dir-long" : "dir-short";
    const promoted = r.promoted_signal_id
      ? ' <span class="badge-promoted">✓ confirmada</span>' : "";
    return `<div class="radar-item">
      <div class="head">
        <span class="when">${esc(new Date(r.created_at).toLocaleString("pt-BR"))}</span>
        <span class="${dirClass}">${r.direction === "long" ? "LONG" : "SHORT"}</span>
        <span>${esc(r.symbol)} · ${esc(r.timeframe)} · ${esc(r.score)}/${esc(r.max_score)}</span>${promoted}
      </div>
      <div class="missing">${esc(r.missing)} @ ${fmt(r.price)}</div>
      <a data-symbol="${esc(r.symbol)}" data-tf="${esc(r.timeframe)}">abrir gráfico →</a>
    </div>`;
  }

  async function loadRadar() {
    const rows = await api("/api/radar?limit=40");
    $("radar-list").innerHTML = rows.length
      ? rows.map(radarItemHtml).join("")
      : '<span class="muted">Nenhuma formação detectada ainda — o radar acumula avisos aqui.</span>';
  }

  // link "abrir gráfico" do radar (delegação de evento)
  $("radar-list").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-symbol]");
    if (!a) return;
    const pairSel = $("pair"), tfSel = $("timeframe");
    if ([...pairSel.options].some((o) => o.value === a.dataset.symbol)) {
      pairSel.value = a.dataset.symbol;
    }
    if ([...tfSel.options].some((o) => o.value === a.dataset.tf)) {
      tfSel.value = a.dataset.tf;
    }
    refreshAll();
  });

  async function loadSignals() {
    const rows = await api("/api/signals?limit=12");
    $("signals-list").innerHTML = rows.length
      ? rows.map((s) => signalHtml(s, false)).join("")
      : '<span class="muted">Nenhum sinal registrado ainda.</span>';
  }

  async function loadPortfolio() {
    const { summary, open_positions } = await api("/api/portfolio");
    $("equity-badge").innerHTML =
      `Equity <b>${fmt(summary.equity)} USDT</b> · PnL ` +
      `<b class="${summary.realized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}">${fmt(summary.realized_pnl)}</b>`;
    const rows = [
      ["Trades fechados", summary.closed_trades],
      ["Taxa de acerto", summary.win_rate == null ? "—" : summary.win_rate + "%"],
      ["Profit factor", summary.profit_factor ?? "—"],
      ["Posições abertas", summary.open_positions],
    ]
      .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
      .join("");
    const open = open_positions.length
      ? open_positions
          .map(
            (p) => `<div class="sig"><div class="head">
              <span class="${p.direction === "long" ? "dir-long" : "dir-short"}">${esc(p.direction.toUpperCase())}</span>
              <span>${esc(p.symbol)} · ${esc(p.timeframe)}</span></div>
              <div class="lvls">Entrada ${fmt(p.entry)} · Stop ${fmt(p.stop)} · Resta ${fmt(p.remaining_size)}</div></div>`
          )
          .join("")
      : "";
    $("portfolio-body").innerHTML = `<table class="mini">${rows}</table>${open}`;
  }

  async function loadExecution() {
    const st = await api("/api/execution");
    $("exec-mode").value = st.mode;
    $("exec-pause").textContent = st.paused ? "Retomar" : "Pausar";
    const b = st.breaker;
    const pausedNote = st.paused
      ? `<span class="paused">⏸ pausada${st.pause_reason ? " — " + esc(st.pause_reason) : ""}</span><br>`
      : "";
    const approvals = (st.pending_approvals || []).map((a) =>
      `<div class="approval">
        <span class="${a.direction === "long" ? "dir-long" : "dir-short"}">${a.direction === "long" ? "LONG" : "SHORT"}</span>
        ${esc(a.symbol)} · ${esc(a.timeframe)} · entrada ${fmt(a.entry)} · ${esc(a.suggested_leverage)}x
        <div class="btns">
          <button class="ok" data-approval="${esc(a.id)}" data-decision="approve">✓ Aprovar</button>
          <button class="no" data-approval="${esc(a.id)}" data-decision="reject">✗ Rejeitar</button>
        </div>
      </div>`
    ).join("");
    $("execution-body").innerHTML =
      `<div class="exec-status">${pausedNote}` +
      `Executor: <b>${esc(st.executor)}</b> · Hoje: ${b.entries_today}/${b.max_trades_per_day} entradas · ` +
      `PnL ${fmt(b.pnl_today)} <span class="muted">(limite −${fmt(b.max_daily_loss)})</span></div>` +
      (approvals || '<div class="muted" style="margin-top:6px">Sem aprovações pendentes.</div>');
  }

  $("exec-mode").addEventListener("change", async () => {
    await api("/api/execution/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: $("exec-mode").value }),
    });
    loadExecution();
  });

  $("exec-pause").addEventListener("click", async () => {
    const st = await api("/api/execution");
    await api(st.paused ? "/api/execution/resume" : "/api/execution/pause", { method: "POST" });
    loadExecution();
  });

  $("execution-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-approval]");
    if (!btn) return;
    btn.disabled = true;
    try {
      await api(`/api/approvals/${btn.dataset.approval}/${btn.dataset.decision}`, { method: "POST" });
    } catch (err) {
      alert("Aprovação: " + err.message);
    }
    loadExecution();
    loadPortfolio();
  });

  async function refreshAll() {
    try {
      await Promise.all([
        loadChart(), loadAnalysis(), loadSignals(),
        loadPortfolio(), loadRadar(), loadExecution(),
      ]);
    } catch (err) {
      $("analysis-box").textContent = "Erro: " + err.message;
    }
  }

  $("refresh").addEventListener("click", refreshAll);
  $("pair").addEventListener("change", refreshAll);
  $("timeframe").addEventListener("change", refreshAll);
  $("scan").addEventListener("click", async () => {
    const btn = $("scan");
    btn.disabled = true;
    btn.textContent = "Escaneando…";
    try {
      await api("/api/scan", { method: "POST" });
      await refreshAll();
    } catch (err) {
      alert("Erro no scan: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Escanear tudo";
    }
  });

  // ---------- tempo real (SSE) ----------
  let sseAlive = false;
  let lastTickAt = 0;

  function updateLatencyBadge() {
    const badge = $("latency-badge");
    if (!sseAlive || !lastTickAt) {
      badge.textContent = "sem stream — polling";
      badge.className = "latency stale";
      return;
    }
    const age = (Date.now() - lastTickAt) / 1000;
    badge.textContent = `ao vivo · ${age < 1 ? "<1" : age.toFixed(0)}s`;
    badge.className = "latency " + (age <= 10 ? "live" : "stale");
  }

  function onTick(d) {
    lastTickAt = Date.now();
    if (d.symbol !== $("pair").value || !lastCandle) return;
    // atualiza o candle em formação no cliente (aspecto de corretora)
    const bucket = Math.floor(d.ts / 1000 / tfSeconds) * tfSeconds;
    if (bucket > lastCandle.time) {
      lastCandle = { time: bucket, open: d.price, high: d.price, low: d.price, close: d.price };
    } else {
      lastCandle.close = d.price;
      lastCandle.high = Math.max(lastCandle.high, d.price);
      lastCandle.low = Math.min(lastCandle.low, d.price);
    }
    candles.update(lastCandle);
  }

  function onCandle(d) {
    if (d.symbol !== $("pair").value || d.timeframe !== $("timeframe").value) return;
    candles.update(d.candle);   // consolida o candle fechado
    lastCandle = {
      time: d.candle.time + d.tf_seconds,
      open: d.candle.close, high: d.candle.close,
      low: d.candle.close, close: d.candle.close,
    };
  }

  function connectStream() {
    const token = getToken();
    const es = new EventSource(
      "/api/stream" + (token ? "?token=" + encodeURIComponent(token) : "")
    );
    es.onopen = () => { sseAlive = true; };
    es.onerror = () => { sseAlive = false; };  // EventSource reconecta sozinho
    es.addEventListener("tick", (e) => onTick(JSON.parse(e.data)));
    es.addEventListener("candle", (e) => onCandle(JSON.parse(e.data)));
    es.addEventListener("signal", () => { loadSignals(); loadAnalysis(); loadPortfolio(); loadRadar(); });
    es.addEventListener("position", () => loadPortfolio());
    es.addEventListener("execution", () => loadExecution());
    es.addEventListener("radar", (e) => {
      const d = JSON.parse(e.data);
      const list = $("radar-list");
      const empty = list.querySelector(".muted");
      if (empty) list.innerHTML = "";
      list.insertAdjacentHTML("afterbegin", radarItemHtml(d.radar));
    });
    es.addEventListener("universe", (e) => {
      const d = JSON.parse(e.data);
      setPairOptions(d.pairs); // universo do screener mudou
    });
  }

  loadConfig().then(refreshAll);
  connectStream();
  setInterval(updateLatencyBadge, 1000);
  // fallback: só faz polling completo quando o stream não está vivo
  setInterval(() => { if (!sseAlive) refreshAll(); }, 60000);
})();
