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

  function clearPriceLines() {
    priceLines.forEach((pl) => candles.removePriceLine(pl));
    priceLines = [];
  }

  function drawSignalLevels(signal) {
    clearPriceLines();
    if (!signal) return;
    const mk = (price, title, color, style) =>
      priceLines.push(
        candles.createPriceLine({
          price, title, color,
          lineWidth: 1,
          lineStyle: style ?? LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
        })
      );
    mk(signal.entry, "entrada", "#c3c2b7", LightweightCharts.LineStyle.Solid);
    mk(signal.stop, "stop", "#e66767");
    signal.targets.forEach((t, i) => mk(t, "TP" + (i + 1), "#199e70"));
  }

  // ---------- estado ----------
  let cfg = { pairs: ["BTC/USDT"], timeframes: ["4h"] };

  async function api(path, opts) {
    const resp = await fetch(path, opts);
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

  async function loadConfig() {
    cfg = await api("/api/config");
    const pairSel = $("pair"), tfSel = $("timeframe");
    pairSel.innerHTML = cfg.pairs.map((p) => `<option>${esc(p)}</option>`).join("");
    tfSel.innerHTML = cfg.timeframes.map((t) => `<option>${esc(t)}</option>`).join("");
    tfSel.value = cfg.timeframes.includes("4h") ? "4h" : cfg.timeframes[0];
  }

  async function loadChart() {
    const symbol = $("pair").value, timeframe = $("timeframe").value;
    const rows = await api(
      `/api/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=300`
    );
    candles.setData(rows.map((r) => ({
      time: r.time, open: r.open, high: r.high, low: r.low, close: r.close,
    })));
    emaFast.setData(rows.filter((r) => r.ema_fast != null).map((r) => ({ time: r.time, value: r.ema_fast })));
    emaSlow.setData(rows.filter((r) => r.ema_slow != null).map((r) => ({ time: r.time, value: r.ema_slow })));
    chart.timeScale().fitContent();
  }

  function renderAnalysis(ctx) {
    const box = $("analysis-box");
    const structureLabel = { uptrend: "Alta", downtrend: "Baixa", range: "Lateral" }[ctx.structure] || ctx.structure;
    const lines = [
      `Preço: ${fmt(ctx.price)}   Estrutura: ${structureLabel}   RSI: ${fmt(ctx.rsi, 1)}`,
      `Suporte: ${fmt(ctx.support)}   Resistência: ${fmt(ctx.resistance)}`,
      `Confluência — LONG ${ctx.long_score}/${ctx.max_score} · SHORT ${ctx.short_score}/${ctx.max_score}`,
    ];
    if (ctx.patterns && ctx.patterns.length) lines.push(`Padrões: ${ctx.patterns.join(", ")}`);
    box.textContent = lines.join("\n");
  }

  function signalHtml(s, withRationale) {
    const dirClass = s.direction === "long" ? "dir-long" : "dir-short";
    const dirLabel = s.direction === "long" ? "LONG ▲" : "SHORT ▼";
    const kind = s.trade_type === "swing" ? "Swing" : "Day trade";
    const rationale =
      withRationale && s.rationale && s.rationale.length
        ? `<ul class="rationale">${s.rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
        : "";
    return `<div class="sig">
      <div class="head"><span class="${dirClass}">${dirLabel}</span>
        <span>${esc(s.symbol)} · ${esc(s.timeframe)} · ${kind} · ${esc(s.score)}/${esc(s.max_score)}</span></div>
      <div class="lvls">Entrada ${fmt(s.entry)} · Stop ${fmt(s.stop)}<br>
        Alvos ${s.targets.map((t) => fmt(t)).join(" / ")}</div>
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
    $("signal-body").innerHTML = signal
      ? signalHtml(signal, true)
      : '<span class="muted">Sem sinal — confluência insuficiente no momento.</span>';
  }

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

  async function refreshAll() {
    try {
      await Promise.all([loadChart(), loadAnalysis(), loadSignals(), loadPortfolio()]);
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

  loadConfig().then(refreshAll);
  setInterval(refreshAll, 60000); // polling leve
})();
