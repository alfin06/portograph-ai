// PortoGraph page scripts. Charts use TradingView Lightweight Charts 4.2 (vendored in
// /static/vendor). No inline scripts anywhere: the Content-Security-Policy forbids them,
// so pages declare what they need with data in the HTML and this file wires it up.
(function () {
  let LC;   // the chart library loads after this file; looked up when a chart is drawn
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `--series-${i}`);

  function baseOptions() {
    return {
      autoSize: true,
      layout: { background: { color: css('--surface-1') }, textColor: css('--text-secondary'),
                fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: css('--grid') } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, lockVisibleTimeRangeOnResize: true },
      crosshair: { mode: LC.CrosshairMode.Normal },
      handleScale: { axisPressedMouseMove: true },
    };
  }

  // Rebuild when the OS switches light/dark, so colors come from the new tokens.
  function themed(build) {
    let destroy = build();
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      destroy(); destroy = build();
    });
  }

  function tooltip(el) {
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    el.appendChild(tip);
    return {
      show(html, point) {
        tip.innerHTML = html;
        tip.style.display = 'block';
        const w = tip.offsetWidth, h = tip.offsetHeight;
        let x = point.x + 14, y = point.y + 14;
        if (x + w > el.clientWidth) x = point.x - w - 14;
        if (y + h > el.clientHeight) y = point.y - h - 14;
        tip.style.left = Math.max(0, x) + 'px';
        tip.style.top = Math.max(0, y) + 'px';
      },
      hide() { tip.style.display = 'none'; },
      remove() { tip.remove(); },
    };
  }

  function pressed(group, btn) {
    group.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
  }

  // --- stock page: candles, volume, moving averages, valuation lines, buy zones -------
  function priceChart(elId, dataId) {
    const el = document.getElementById(elId);
    const data = JSON.parse(document.getElementById(dataId).textContent);
    if (!data.candles.length) { el.textContent = 'No prices stored yet.'; return; }
    const digits = data.idr ? 0 : 2;
    const fmt = (v) => v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const rangeGroup = document.querySelector('[aria-label="Range"]');
    const metricSel = document.getElementById('band-metric');
    const smaBox = document.getElementById('show-sma');
    let range = 252;

    themed(() => {
      const chart = LC.createChart(el, baseOptions());
      const candles = chart.addCandlestickSeries({
        upColor: css('--candle-up'), downColor: css('--candle-down'), borderVisible: false,
        wickUpColor: css('--candle-up'), wickDownColor: css('--candle-down'),
        priceFormat: { type: 'price', precision: digits, minMove: data.idr ? 1 : 0.01 },
      });
      candles.setData(data.candles);
      const vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' },
                                             color: css('--grid'), lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(data.volume);
      const smaOpts = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
      const sma50 = chart.addLineSeries({ ...smaOpts, color: css('--series-1') });
      const sma200 = chart.addLineSeries({ ...smaOpts, color: css('--series-4') });
      sma50.setData(data.sma50); sma200.setData(data.sma200);
      const setSma = () => { const v = !smaBox || smaBox.checked; sma50.applyOptions({ visible: v }); sma200.applyOptions({ visible: v }); };
      setSma();

      for (const z of data.zones) {
        for (const [p, t] of [[z.high, `Zone #${z.id}`], [z.low, '']]) {
          candles.createPriceLine({ price: p, color: css('--zone'), lineWidth: 2,
                                    lineStyle: LC.LineStyle.Solid, axisLabelVisible: true, title: t });
        }
      }

      let bandLines = [];
      const BAND = { 10: ['--band-cheap', 'cheapest 10%'], 25: ['--band-cheap', 'cheap 25%'], 50: ['--band-mid', 'median'],
                     75: ['--band-rich', '75%'], 90: ['--band-rich', '90%'] };
      const drawBands = () => {
        bandLines.forEach((l) => candles.removePriceLine(l));
        bandLines = [];
        const b = metricSel && data.bands[metricSel.value];
        if (!b) return;
        for (const [pct, price] of Object.entries(b.prices)) {
          const [color, label] = BAND[pct];
          bandLines.push(candles.createPriceLine({ price, color: css(color), lineWidth: 1,
            lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: `${b.label} ${label}` }));
        }
      };
      drawBands();

      const applyRange = () => {
        const n = data.candles.length;
        if (!range || range >= n) chart.timeScale().fitContent();
        else chart.timeScale().setVisibleLogicalRange({ from: n - range, to: n - 1 });
      };
      // autoSize measures the container on the next frame; set the range after that
      requestAnimationFrame(() => requestAnimationFrame(applyRange));

      const tip = tooltip(el);
      const byTime = new Map(data.candles.map((c, i) => [c.time, i]));
      chart.subscribeCrosshairMove((param) => {
        const c = param.time && param.seriesData.get(candles);
        if (!c || !param.point) { tip.hide(); return; }
        const i = byTime.get(param.time);
        const prev = i > 0 ? data.candles[i - 1].close : null;
        const chg = prev ? ((c.close / prev - 1) * 100) : null;
        const chgTxt = chg == null ? '' : ` <span class="${chg >= 0 ? 'up' : 'down'}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>`;
        tip.show(`<b>${param.time}</b><br>Open ${fmt(c.open)} · High ${fmt(c.high)}<br>Low ${fmt(c.low)} · Close <b>${fmt(c.close)}</b>${chgTxt}`, param.point);
      });

      const onRange = (e) => {
        const btn = e.target.closest('button'); if (!btn) return;
        range = Number(btn.dataset.range); pressed(rangeGroup, btn); applyRange();
      };
      rangeGroup.addEventListener('click', onRange);
      metricSel && metricSel.addEventListener('change', drawBands);
      smaBox && smaBox.addEventListener('change', setSma);
      return () => {
        rangeGroup.removeEventListener('click', onRange);
        metricSel && metricSel.removeEventListener('change', drawBands);
        smaBox && smaBox.removeEventListener('change', setSma);
        tip.remove(); chart.remove();
      };
    });
  }

  // --- compare page: growth of 100, one line per symbol ---------------------------------
  function compareChart(elId, dataId) {
    const el = document.getElementById(elId);
    const data = JSON.parse(document.getElementById(dataId).textContent);
    const symbols = Object.keys(data);
    const group = document.querySelector('[aria-label="Currency"]');
    let ccy = 'idr';
    const legend = document.createElement('div');
    legend.className = 'series-legend';
    el.before(legend);

    themed(() => {
      const chart = LC.createChart(el, { ...baseOptions(), localization: { priceFormatter: (v) => v.toFixed(1) } });
      const lines = symbols.map((sym, i) => {
        const color = css(SERIES[i % SERIES.length]);
        return { sym, color, s: chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, title: sym }) };
      });
      legend.innerHTML = lines.map((l) => `<span style="--c:${l.color}">${l.sym}</span>`).join('');
      const draw = () => { lines.forEach((l) => l.s.setData(data[l.sym][ccy] || [])); chart.timeScale().fitContent(); };
      draw();
      requestAnimationFrame(() => requestAnimationFrame(() => chart.timeScale().fitContent()));
      const tip = tooltip(el);
      chart.subscribeCrosshairMove((param) => {
        if (!param.time || !param.point) { tip.hide(); return; }
        const rows = lines.map((l) => {
          const p = param.seriesData.get(l.s);
          return p ? `<span style="color:${l.color}">■</span> ${l.sym} <b>${p.value.toFixed(1)}</b> (${p.value >= 100 ? '+' : ''}${(p.value - 100).toFixed(1)}%)` : '';
        }).filter(Boolean);
        if (!rows.length) { tip.hide(); return; }
        tip.show(`<b>${param.time}</b><br>${rows.join('<br>')}`, param.point);
      });
      const onCcy = (e) => {
        const btn = e.target.closest('button'); if (!btn) return;
        ccy = btn.dataset.ccy; pressed(group, btn); draw();
      };
      if (group) group.addEventListener('click', onCcy);
      return () => { if (group) group.removeEventListener('click', onCcy); tip.remove(); chart.remove(); };
    });
  }

  // Portfolio value per day (one point per digest) against what it cost
  function portfolioChart(elId, dataId) {
    const el = document.getElementById(elId);
    const rows = JSON.parse(document.getElementById(dataId).textContent);
    const juta = (v) => `Rp ${(v / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} juta`;
    themed(() => {
      const chart = LC.createChart(el, { ...baseOptions(), localization: { priceFormatter: (v) => (v / 1e6).toFixed(0) + ' jt' } });
      const value = chart.addLineSeries({ color: css('--series-1'), lineWidth: 2, priceLineVisible: false, title: 'Value' });
      const cost = chart.addLineSeries({ color: css('--band-mid'), lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
                                         priceLineVisible: false, lastValueVisible: false, title: 'Cost' });
      value.setData(rows.map((r) => ({ time: r.time, value: r.value })));
      cost.setData(rows.map((r) => ({ time: r.time, value: r.cost })));
      chart.timeScale().fitContent();
      requestAnimationFrame(() => requestAnimationFrame(() => chart.timeScale().fitContent()));
      const tip = tooltip(el);
      chart.subscribeCrosshairMove((param) => {
        const v = param.time && param.point && param.seriesData.get(value);
        const c = v && param.seriesData.get(cost);
        if (!v || !c) { tip.hide(); return; }
        const gain = v.value - c.value;
        tip.show(`<b>${param.time}</b><br>Value <b>${juta(v.value)}</b><br>Cost ${juta(c.value)}<br>` +
                 `Unrealised ${gain >= 0 ? '+' : '−'}${juta(Math.abs(gain))}`, param.point);
      });
      return () => { tip.remove(); chart.remove(); };
    });
  }

  // Forms with data-confirm ask before submitting (e.g. deleting a screen)
  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset && e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  window.addEventListener('DOMContentLoaded', () => {
    LC = window.LightweightCharts;
    if (!LC) return;
    if (document.getElementById('chart-data')) priceChart('chart', 'chart-data');
    if (document.getElementById('cmp-data')) compareChart('cmp-chart', 'cmp-data');
    if (document.getElementById('port-data')) portfolioChart('port-chart', 'port-data');
    if (document.getElementById('bench-data')) compareChart('bench-chart', 'bench-data');
  });
})();
