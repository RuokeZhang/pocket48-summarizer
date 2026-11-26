(() => {
  "use strict";

  const W = 720;
  const PAD = 32;
  const CW = W - PAD * 2;
  const SCALE = 2;
  const FONT = "FusionPixel";
  const C = {
    navy: "#262840", navyPanel: "#33365A", navyLine: "#4A4D78",
    cream: "#FFF4DE", creamPanel: "#F6E7C8", creamLine: "#E2CFA8",
    ink: "#262840", inkMuted: "#5E5872", light: "#FFF4DE", lightMuted: "#BDB8D6",
    pink: "#F49AB0", accent: "#C2456E", room: "#3A7FC2", danmaku: "#C2456E",
    missed: "#E2CFA8", yearOld: "#B0657F", yearNew: "#F49AB0"
  };
  const HEAT = [[0, C.creamPanel], [1, "#F3C1CC"], [5, "#E58AA3"], [15, "#C2456E"], [40, "#7A1F45"]];
  const PHRASE_LABELS = [["哈哈", "带“哈哈”"], ["笑死", "带“笑死”"], ["可爱", "夸“可爱”"], ["好看", "夸“好看”"], ["辛苦", "说“辛苦”"], ["晚安", "说“晚安”"], ["姐", "带“姐”字"]];

  // ---------- drawing primitives ----------
  const font = (size) => `${size}px "${FONT}"`;
  const measure = (p, text, size) => { p.ctx.font = font(size); return p.ctx.measureText(text).width; };
  // Snap to whole pixels so adjacent pixel-art rows never leave anti-aliased seams.
  const rect = (p, x, y, w, h, color) => {
    if (!p.draw || w <= 0 || h <= 0) return;
    const left = Math.round(x), top = Math.round(y);
    p.ctx.fillStyle = color;
    p.ctx.fillRect(left, top, Math.round(x + w) - left, Math.round(y + h) - top);
  };
  const text = (p, value, x, y, size, color, align = "left") => {
    if (!p.draw) return;
    p.ctx.font = font(size);
    p.ctx.fillStyle = color;
    p.ctx.textAlign = align;
    p.ctx.textBaseline = "top";
    p.ctx.fillText(value, x, y);
  };
  const wrap = (p, value, maxWidth, size) => {
    const lines = [];
    let line = "";
    for (const char of Array.from(String(value))) {
      if (char === "\n") { lines.push(line); line = ""; continue; }
      if (line && measure(p, line + char, size) > maxWidth) { lines.push(line); line = char; } else { line += char; }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  };
  const para = (p, value, x, y, maxWidth, size, color, lineHeight = size * 1.5) => {
    const lines = wrap(p, value, maxWidth, size);
    lines.forEach((line, index) => text(p, line, x, y + index * lineHeight, size, color));
    return y + lines.length * lineHeight;
  };
  const frame = (p, x, y, w, h, fill, border) => {
    rect(p, x, y, w, h, fill);
    rect(p, x, y - 4, w, 4, border);
    rect(p, x, y + h, w, 4, border);
    rect(p, x - 4, y, 4, h, border);
    rect(p, x + w, y, 4, h, border);
  };
  const background = (p, y, h, color) => rect(p, 0, y, W, h, color);
  const divider = (p, y, top, bottom) => {
    background(p, y, 24, top);
    for (let column = 0; column < W / 8; column += 1) {
      const step = [8, 16, 8, 0][column % 4] + 8;
      rect(p, column * 8, y + 24 - step, 8, step, bottom);
    }
    return y + 24;
  };
  const fmt = (value) => Number(value).toLocaleString("zh-CN");
  const chinaDate = (iso) => (iso || "").slice(0, 10).replaceAll("-", ".");
  const scopeLabel = (scope) => (scope === "2026" ? "2026" : "2025—2026");

  // ---------- pixel scene ----------
  const seeded = (seed) => () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const BUBBLE = [".ooooooo.", "owwwwwwwo", "owswswswo", "owwwwwwwo", ".ooooooo.", "..oo.....", "..o......"];
  const HEART = [".rr.rr.", "rrrrrrr", "rrrrrrr", ".rrrrr.", "..rrr..", "...r..."];

  const scene = (p, y) => {
    const gw = 180, gh = 66, unit = 4;
    if (p.draw) {
      const grid = Array.from({ length: gh }, () => new Array(gw).fill(null));
      const fill = (x0, y0, x1, y1, color) => {
        for (let yy = Math.max(0, y0); yy < Math.min(gh, y1); yy += 1)
          for (let xx = Math.max(0, x0); xx < Math.min(gw, x1); xx += 1) grid[yy][xx] = color;
      };
      const stamp = (rows, colors, x0, y0) => rows.forEach((row, ry) => Array.from(row).forEach((char, rx) => {
        if (char !== "." && y0 + ry < gh && x0 + rx < gw) grid[y0 + ry][x0 + rx] = colors[char];
      }));
      [[0, 14, "#1E1F36"], [14, 26, "#262840"], [26, 36, "#34315E"], [36, 44, "#4E3F78"], [44, 50, "#7B5389"], [50, 66, "#B66C8A"]]
        .forEach(([top, bottom, color]) => fill(0, top, gw, bottom, color));
      const random = seeded(48);
      for (let index = 0; index < 44; index += 1) {
        const sx = Math.floor(random() * gw), sy = Math.floor(random() * 34);
        grid[sy][sx] = random() < 0.6 ? "#FFF4DE" : "#BDB8D6";
      }
      for (let yy = 5; yy <= 17; yy += 1)
        for (let xx = 98; xx <= 110; xx += 1)
          if ((xx - 104) ** 2 + (yy - 11) ** 2 <= 38) grid[yy][xx] = "#FFE9B8";
      for (let xx = 0; xx < gw; xx += 1) fill(xx, Math.floor(40 - 6 * Math.sin(xx / 13 + 3) - 4 * Math.sin(xx / 5.3)), xx + 1, gh, "#3E3668");
      for (let xx = 0; xx < gw; xx += 1) fill(xx, Math.floor(47 - 4 * Math.sin(xx / 9 + 1) - 2 * Math.sin(xx / 3.7)), xx + 1, gh, "#4C3F70");
      fill(0, 56, gw, gh, "#253A5C");
      const waves = seeded(9);
      for (let yy = 57; yy < gh; yy += 2)
        for (let index = 0; index < 7; index += 1) { const wx = Math.floor(waves() * (gw - 6)); fill(wx, yy, wx + 2 + Math.floor(waves() * 4), yy + 1, "#3D628A"); }
      for (let xx = 0; xx < 78; xx += 1) {
        const top = Math.max(30, Math.min(Math.floor(30 + 0.012 * (xx - 24) ** 2), 56));
        fill(xx, top, xx + 1, gh, "#2E5650");
        fill(xx, top, xx + 1, top + 1, "#5E9B7A");
      }
      [[4, 36], [50, 40], [62, 46], [18, 26]].forEach(([tx, ty]) => {
        fill(tx + 2, ty + 4, tx + 3, ty + 8, "#5A3A2A");
        [[1, 4], [0, 5], [0, 5], [1, 4]].forEach(([left, right], offset) => fill(tx + left, ty + offset, tx + right, ty + offset + 1, "#F49AB0"));
      });
      for (let xx = 116; xx < gw; xx += 1) fill(xx, Math.max(48, Math.min(Math.floor(50 + 0.02 * (xx - 152) ** 2), 56)), xx + 1, 57, "#2E5650");
      fill(126, 18, 176, 48, "#1B1C2E");
      fill(128, 20, 174, 46, "#2F2A55");
      fill(129, 21, 137, 25, "#E5484D");
      [[142, 18, "#FFF4DE"], [132, 26, "#F49AB0"], [150, 20, "#9AD1E0"], [136, 14, "#FFD166"], [146, 24, "#FFF4DE"], [131, 20, "#F49AB0"]]
        .forEach(([lx, length, color], index) => fill(lx, 28 + index * 3, Math.min(lx + length, 173), 29 + index * 3, color));
      stamp(HEART, { r: "#C2456E" }, 166, 38);
      fill(149, 48, 153, 53, "#1B1C2E");
      fill(141, 53, 161, 55, "#1B1C2E");
      [[24, 12, C.room], [56, 6, C.danmaku], [92, 22, "#D9902F"]].forEach(([bx, by, color]) => stamp(BUBBLE, { o: "#1B1C2E", w: "#FFF4DE", s: color }, bx, by));
      grid.forEach((row, ry) => {
        let start = 0;
        for (let column = 1; column <= gw; column += 1) {
          if (column === gw || row[column] !== row[start]) {
            rect(p, start * unit, y + ry * unit, (column - start) * unit, unit, row[start]);
            start = column;
          }
        }
      });
      text(p, "LIVE", 130 * unit, y + 21 * unit + 2, 12, "#FFFFFF");
    }
    return y + gh * unit;
  };

  // ---------- sections ----------
  const chapter = (p, y, dark, tag, english, title, lead, sub) => {
    const ink = dark ? C.light : C.ink, muted = dark ? C.lightMuted : C.inkMuted;
    y += 12;
    const tagWidth = measure(p, tag, 24) + 24;
    rect(p, PAD, y, tagWidth, 36, dark ? C.pink : C.ink);
    text(p, tag, PAD + 12, y + 6, 24, dark ? C.navy : C.light);
    text(p, english, W - PAD, y + 6, 24, muted, "right");
    y += 52;
    y = para(p, title, PAD, y, CW, 48, ink, 60);
    if (lead) y = para(p, lead, PAD, y + 12, CW, 36, dark ? C.pink : C.accent, 48);
    if (sub) y = para(p, sub, PAD, y + 8, CW, 24, muted, 36);
    return y;
  };

  const header = (p, y, report) => {
    const top = y;
    const total = report.totals.room + report.totals.danmaku;
    const name = report.display_name || "口袋48的你";
    let size = 72;
    while (size > 36 && measure(p, name, size) > CW) size -= 12;
    const nameLines = wrap(p, name, CW, size);
    const height = 24 + 36 + 24 + nameLines.length * size * 1.15 + 12 + 48 + 12 + 36 + 16;
    background(p, top, height, C.navy);
    y += 24;
    text(p, "POCKET48 · 年度报告", PAD, y, 24, C.lightMuted);
    text(p, scopeLabel(report.scope), W - PAD, y, 24, C.lightMuted, "right");
    y += 60;
    nameLines.forEach((line, index) => text(p, line, PAD, y + index * size * 1.15, size, C.light));
    y += nameLines.length * size * 1.15 + 12;
    text(p, `${fmt(total)}句话，说给${report.member_name}和大家`, PAD, y, 36, C.pink);
    y += 60;
    text(p, `口袋48年度报告 · ${scopeLabel(report.scope)}`, PAD, y, 24, C.lightMuted);
    return Math.ceil(top + height);
  };

  const hud = (p, y, report) => {
    const t = report.totals;
    const tiles = [
      [fmt(t.danmaku), "句直播弹幕", C.danmaku],
      [fmt(t.room), "句房间留言", C.room],
      [t.lives ? `${t.attended}/${t.lives}` : "—", `场${report.member_name}直播里有你`, null],
      [`${t.active_days}`, `天有发言（共${t.total_days}天）`, null]
    ];
    const height = 2 * 120 + 28 + 68;
    background(p, y, height, C.navy);
    const tileWidth = (W - 72 - 28) / 2;
    tiles.forEach(([value, label, chip], index) => {
      const x = 36 + (index % 2) * (tileWidth + 28), ty = y + 28 + Math.floor(index / 2) * 148;
      frame(p, x, ty, tileWidth, 120, C.navyPanel, C.navyLine);
      let vx = x + 20;
      if (chip) { rect(p, vx, ty + 32, 20, 20, chip); vx += 34; }
      let size = 60;
      while (size > 36 && measure(p, value, size) > tileWidth - (vx - x) - 20) size -= 12;
      text(p, value, vx, ty + 12 + (60 - size) / 2, size, C.light);
      wrap(p, label, tileWidth - 40, 24).slice(0, 1).forEach((line) => text(p, line, x + 20, ty + 80, 24, C.lightMuted));
    });
    return y + height;
  };

  const monthlyChart = (p, y, report) => {
    const months = report.monthly;
    const left = 44, top = 56, bottom = 80, plotWidth = CW - left;
    const columnWidth = plotWidth / months.length;
    const barWidth = Math.max(8, Math.min(22, Math.floor(columnWidth * 0.7)));
    const maxTotal = Math.max(1, ...months.map((m) => m.room + m.danmaku));
    const plotHeight = 240;
    const baseline = y + top + plotHeight;
    if (p.draw) {
      [[C.room, "房间留言"], [C.danmaku, "直播弹幕"]].forEach(([color, label], index) => {
        rect(p, PAD + index * 160, y + 8, 16, 16, color);
        text(p, label, PAD + index * 160 + 24, y + 4, 24, C.ink);
      });
      text(p, "单位：句", W - PAD, y + 4, 24, C.inkMuted, "right");
      const gridStep = niceStep(maxTotal);
      for (let value = gridStep; value <= maxTotal; value += gridStep) {
        const gy = Math.round(baseline - (value / maxTotal) * plotHeight);
        for (let dx = PAD + left; dx < PAD + CW; dx += 8) rect(p, dx, gy, 4, 2, C.creamLine);
        text(p, String(value), PAD, gy - 12, 24, C.inkMuted);
      }
      let peakIndex = 0;
      months.forEach((m, index) => { if (m.room + m.danmaku > months[peakIndex].room + months[peakIndex].danmaku) peakIndex = index; });
      months.forEach((m, index) => {
        const x = Math.round(PAD + left + index * columnWidth + (columnWidth - barWidth) / 2);
        let stackTop = baseline;
        [[m.room, C.room], [m.danmaku, C.danmaku]].forEach(([value, color]) => {
          if (!value) return;
          const h = Math.max(2, Math.round((value / maxTotal) * plotHeight / 2) * 2);
          stackTop -= h;
          rect(p, x, stackTop, barWidth, h > 4 ? h - 2 : h, color);
        });
        if (index === peakIndex && m.room + m.danmaku > 0) text(p, String(m.room + m.danmaku), x + barWidth / 2, stackTop - 30, 24, C.ink, "center");
        const month = Number(m.month.slice(5));
        if ([1, 4, 7, 10].includes(month)) text(p, String(month), x + barWidth / 2, baseline + 10, 24, C.inkMuted, "center");
        if (month === 1) text(p, m.month.slice(0, 4), x, baseline + 44, 24, C.ink);
      });
      rect(p, PAD + left, baseline + 2, plotWidth, 2, C.ink);
    }
    return baseline + bottom;
  };

  const niceStep = (max) => {
    const raw = max / 3;
    const magnitude = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
    return [1, 2, 5, 10].map((k) => k * magnitude).find((step) => step >= raw) || magnitude * 10;
  };

  const attendanceChart = (p, y, report) => {
    const months = report.monthly;
    const maxLives = Math.max(1, ...months.map((m) => m.lives));
    const step = Math.max(4, Math.min(18, Math.floor(216 / maxLives)));
    const block = Math.max(2, step - 2);
    const columnWidth = CW / months.length;
    const blockWidth = Math.max(6, Math.min(16, Math.floor(columnWidth * 0.6)));
    const top = 48, plotHeight = maxLives * step, baseline = y + top + plotHeight;
    if (p.draw) {
      rect(p, PAD, y + 8, 16, 16, C.accent); text(p, "你发了弹幕", PAD + 24, y + 4, 24, C.ink);
      rect(p, PAD + 168, y + 8, 16, 16, C.missed); text(p, "没发", PAD + 192, y + 4, 24, C.ink);
      text(p, `每格=${report.member_name}的1场直播`, W - PAD, y + 4, 24, C.inkMuted, "right");
      months.forEach((m, index) => {
        const x = Math.round(PAD + index * columnWidth + (columnWidth - blockWidth) / 2);
        for (let b = 0; b < m.lives; b += 1) rect(p, x, baseline - (b + 1) * step + (step - block), blockWidth, block, b < m.attended ? C.accent : C.missed);
        const month = Number(m.month.slice(5));
        if ([1, 4, 7, 10].includes(month)) text(p, String(month), x + blockWidth / 2, baseline + 10, 24, C.inkMuted, "center");
        if (month === 1) text(p, m.month.slice(0, 4), x, baseline + 44, 24, C.ink);
      });
      rect(p, PAD, baseline + 2, CW, 2, C.ink);
    }
    return baseline + 80;
  };

  const shareBars = (p, y, rows, yearKeys, labelWidth, valueFormatter) => {
    rows.forEach((row) => {
      const blockTop = y;
      text(p, row.label, PAD, blockTop + (yearKeys.length > 1 ? 14 : 0), 24, C.light);
      yearKeys.forEach((year, index) => {
        const ry = blockTop + index * 32;
        if (yearKeys.length > 1) text(p, year, PAD + labelWidth, ry, 24, C.lightMuted);
        const barX = PAD + labelWidth + (yearKeys.length > 1 ? 68 : 0);
        const width = Math.max(4, Math.round(row.shares[index] * 240 / 4) * 4);
        rect(p, barX, ry + 8, width, 18, index === yearKeys.length - 1 ? C.yearNew : C.yearOld);
        text(p, valueFormatter(row, index), barX + width + 12, ry, 24, C.light);
      });
      y += yearKeys.length * 32 + 18;
    });
    return y;
  };

  const delaySection = (p, y, report) => {
    const years = Object.keys(report.delay);
    const maxShare = Math.max(...years.flatMap((year) => {
      const total = report.delay[year].buckets.reduce((sum, b) => sum + b.count, 0) || 1;
      return report.delay[year].buckets.map((b) => b.count / total);
    }), 0.01);
    const rows = report.delay[years[0]].buckets.map((bucket, index) => ({
      label: bucket.label,
      counts: years.map((year) => report.delay[year].buckets[index].count),
      shares: years.map((year) => {
        const total = report.delay[year].buckets.reduce((sum, b) => sum + b.count, 0) || 1;
        return report.delay[year].buckets[index].count / total / maxShare;
      }),
      raw: years.map((year) => {
        const total = report.delay[year].buckets.reduce((sum, b) => sum + b.count, 0) || 1;
        return report.delay[year].buckets[index].count / total;
      })
    }));
    y = shareBars(p, y + 16, rows, years, 148, (row, index) => `${Math.round(row.raw[index] * 100)}%（${row.counts[index]}场）`);
    const tileWidth = (CW - 28) / 2;
    [[`${report.lines_per_live.mean}句`, "到场后平均每场的弹幕"], [`${report.lines_per_live.max}句`, "单场最多"]].forEach(([value, label], index) => {
      const x = PAD + 4 + index * (tileWidth + 28);
      frame(p, x, y + 16, tileWidth - 8, 104, C.navyPanel, C.navyLine);
      text(p, value, x + 16, y + 26, 48, C.pink);
      text(p, label, x + 16, y + 82, 24, C.light);
    });
    return y + 136;
  };

  const heatColor = (count) => HEAT.reduce((color, [threshold, step]) => (count >= threshold ? step : color), HEAT[0][1]);

  const calendar = (p, y, report, year) => {
    const cell = 10, gap = 12;
    const first = new Date(Date.UTC(year, 0, 1));
    const last = new Date(Math.min(Date.UTC(year, 11, 31), Date.parse(`${report.data_end}T00:00:00Z`)));
    const offset = (first.getUTCDay() + 6) % 7;
    const streak = report.longest_streak;
    const activeInYear = Object.keys(report.daily).filter((day) => day.startsWith(String(year))).length;
    text(p, String(year), PAD, y, 36, C.ink);
    text(p, `${activeInYear}天有发言`, W - PAD, y + 10, 24, C.inkMuted, "right");
    const top = y + 84;
    let streakMin = null, streakMax = null;
    for (let d = new Date(first); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
      const dayIndex = Math.round((d - first) / 86400000) + offset;
      const column = Math.floor(dayIndex / 7), row = dayIndex % 7;
      const key = d.toISOString().slice(0, 10);
      rect(p, PAD + column * gap, top + row * gap, cell, cell, heatColor(report.daily[key] || 0));
      if (d.getUTCDate() === 1 && d.getUTCMonth() % 2 === 0) text(p, String(d.getUTCMonth() + 1), PAD + column * gap, top - 30, 24, C.inkMuted);
      if (streak.start && key >= streak.start && key <= streak.end) {
        streakMin = streakMin === null ? column : Math.min(streakMin, column);
        streakMax = streakMax === null ? column : Math.max(streakMax, column);
      }
    }
    if (streakMin !== null) rect(p, PAD + streakMin * gap, top + 7 * gap + 2, (streakMax - streakMin + 1) * gap - 2, 4, C.ink);
    return top + 7 * gap + 24;
  };

  const hoursChart = (p, y, report) => {
    const hours = report.hours;
    const total = hours.reduce((a, b) => a + b, 0) || 1;
    const columnWidth = CW / 24, barWidth = 18, plotHeight = 200;
    const maxShare = Math.max(...hours) / total || 1;
    const baseline = y + 48 + plotHeight;
    let peak = 0;
    hours.forEach((value, hour) => { if (value > hours[peak]) peak = hour; });
    hours.forEach((value, hour) => {
      const share = value / total;
      const h = value ? Math.max(2, Math.round((share / maxShare) * plotHeight / 4) * 4) : 0;
      const x = Math.round(PAD + hour * columnWidth + (columnWidth - barWidth) / 2);
      rect(p, x, baseline - h, barWidth, h, hour === peak ? C.pink : "#B0657F");
      if (hour % 3 === 0) text(p, String(hour), x + barWidth / 2, baseline + 10, 24, C.lightMuted, "center");
      if (hour === peak) text(p, `${Math.round(share * 100)}%`, Math.max(PAD + 24, x + barWidth / 2), baseline - h - 30, 24, C.light, "center");
    });
    rect(p, PAD, baseline + 2, CW, 2, C.lightMuted);
    return baseline + 56;
  };

  const phraseSection = (p, y, report) => {
    const years = Object.keys(report.phrases);
    const maxShare = Math.max(0.01, ...years.flatMap((year) => PHRASE_LABELS.map(([key]) => report.phrases[year][key] / report.phrases[year].total)));
    const rows = PHRASE_LABELS.map(([key, label]) => ({
      label,
      counts: years.map((year) => report.phrases[year][key]),
      raw: years.map((year) => report.phrases[year][key] / report.phrases[year].total),
      shares: years.map((year) => report.phrases[year][key] / report.phrases[year].total / maxShare)
    }));
    y = shareBars(p, y + 16, rows, years, 148, (row, index) => `${(row.raw[index] * 100).toFixed(1)}%（${row.counts[index]}句）`);
    y = para(p, `占比 = 含该词的发言 ÷ 当年发言总数。`, PAD, y, CW, 24, C.lightMuted);
    if (report.ha.count) {
      const haText = `一共打出${fmt(report.ha.count)}个“哈”，最长一次连打${report.ha.longest}个`;
      const lines = wrap(p, haText, CW - 48, 24);
      const h = 72 + lines.length * 36;
      frame(p, PAD + 4, y + 24, CW - 8, h, C.navyPanel, C.navyLine);
      text(p, `${fmt(report.ha.count)}个“哈”`, PAD + 24, y + 36, 48, C.pink);
      lines.forEach((line, index) => text(p, line, PAD + 24, y + 96 + index * 36, 24, C.light));
      y += h + 36;
    }
    return y;
  };

  const nicknameSection = (p, y, report) => {
    const start = Date.parse(`${report.years[0]}-01-01T00:00:00Z`);
    const end = Date.parse(`${report.data_end}T00:00:00Z`) + 86400000;
    report.nicknames.forEach((entry) => {
      y += 16;
      let size = 36;
      while (size > 24 && measure(p, entry.name, size) > CW - 140) size -= 12;
      text(p, entry.name, PAD, y, size, C.ink);
      text(p, `${fmt(entry.count)}句`, W - PAD, y + 8, 24, C.inkMuted, "right");
      y += 52;
      rect(p, PAD, y, CW, 16, C.creamPanel);
      const left = Math.max(0, (Date.parse(`${entry.first}T00:00:00Z`) - start) / (end - start));
      const right = Math.min(1, (Date.parse(`${entry.last}T00:00:00Z`) + 86400000 - start) / (end - start));
      rect(p, PAD + Math.round(left * CW / 4) * 4, y, Math.max(4, Math.round((right - left) * CW / 4) * 4), 16, C.accent);
      y += 24;
      text(p, entry.first === entry.last ? chinaDate(entry.first) : `${chinaDate(entry.first)}—${chinaDate(entry.last)}`, PAD, y, 24, C.inkMuted);
      y += 36;
    });
    text(p, `${report.years[0]}.01`, PAD, y + 4, 24, C.inkMuted);
    text(p, chinaDate(report.data_end), W - PAD, y + 4, 24, C.inkMuted, "right");
    return y + 48;
  };

  const topLivesSection = (p, y, report) => {
    report.top_lives.forEach((live, index) => {
      const quoteLines = live.quotes.flatMap((quote) => wrap(p, `▸ ${quote}`, CW - 64, 24));
      const meta = `${chinaDate(live.started_at)} · ${live.nicknames.join(" → ")}`;
      const metaLines = wrap(p, meta, CW - 40, 24);
      const h = 64 + metaLines.length * 36 + 24 + quoteLines.length * 36 + 24;
      const x = PAD + 4, top = y + 28;
      frame(p, x, top, CW - 8, h, C.creamPanel, C.ink);
      const rank = `No.${index + 1}`;
      rect(p, x + 20, top + 18, measure(p, rank, 24) + 20, 36, C.accent);
      text(p, rank, x + 30, top + 24, 24, "#FFFFFF");
      const titleX = x + 20 + measure(p, rank, 24) + 36;
      const countText = `${live.count}句`;
      const titleRoom = CW - 8 - (titleX - x) - measure(p, countText, 36) - 40;
      const title = wrap(p, `「${live.title}」`, titleRoom, 36)[0];
      text(p, title, titleX, top + 12, 36, C.ink);
      text(p, countText, x + CW - 28, top + 12, 36, C.accent, "right");
      let ly = top + 64;
      metaLines.forEach((line) => { text(p, line, x + 20, ly, 24, C.inkMuted); ly += 36; });
      if (quoteLines.length) {
        rect(p, x + 20, ly + 8, CW - 48, quoteLines.length * 36 + 16, "#2F2A55");
        quoteLines.forEach((line, qi) => text(p, line, x + 36, ly + 16 + qi * 36, 24, C.light));
      }
      y = top + h + 8;
    });
    return y + 24;
  };

  const roomsSection = (p, y, report) => {
    const max = Math.max(1, ...report.other_rooms.map((room) => room.count));
    report.other_rooms.forEach((room) => {
      text(p, room.name, PAD, y + 4, 24, C.light);
      rect(p, PAD + 132, y + 12, Math.max(4, Math.round(room.count / max * 300 / 4) * 4), 18, C.pink);
      text(p, `${room.count}句`, PAD + 452, y + 4, 24, C.light);
      y += 40;
    });
    if (report.small_rooms.rooms) y = para(p, `另有${report.small_rooms.rooms}个房间各1—2句，共${report.small_rooms.messages}句。`, PAD, y + 8, CW, 24, C.lightMuted);
    return y + 16;
  };

  const footer = (p, y, report) => {
    const notes = [
      "数据：口袋48公开回放弹幕、msg48.org 公开房间存档。时间按北京时间。只统计文字发言，不含表情、礼物和翻牌。",
      `弹幕只统计${report.member_name}的直播，并按你用过的昵称在对应时间段内匹配，可能有遗漏。`
    ];
    if (report.notes && report.notes.archive_truncated) notes.push("你的房间留言超过1万条，只统计了最早的1万条。");
    if (report.notes && report.notes.unreadable_lives) notes.push(`有${report.notes.unreadable_lives}场回放的弹幕暂时读不到，没有计入。`);
    const noteText = notes.join("");
    const lines = wrap(p, noteText, CW, 24);
    const h = 220 + lines.length * 36 + 48;
    background(p, y, h, C.navy);
    text(p, "下一句，明年见", W / 2, y + 48, 48, C.light, "center");
    text(p, "p48.ruokezhang.com/report", W / 2, y + 124, 24, C.pink, "center");
    rect(p, PAD, y + 188, CW, 2, C.navyLine);
    lines.forEach((line, index) => text(p, line, PAD, y + 204 + index * 36, 24, C.lightMuted));
    return y + h;
  };

  // Each section knows how to measure itself; the first pass measures, the second draws.
  const layout = (p, report) => {
    let y = 0;
    y = header(p, y, report);
    y = scene(p, y);
    y = hud(p, y, report);
    let dark = true;
    const section = (wantDark, render) => {
      if (dark !== wantDark) { y = divider(p, y, dark ? C.navy : C.cream, wantDark ? C.navy : C.cream); dark = wantDark; }
      const start = y;
      const body = { ...p, draw: false };
      const end = render(body, start) + 24;
      background(p, start, end - start, wantDark ? C.navy : C.cream);
      render(p, start);
      y = end;
    };
    const t = report.totals;
    const peak = report.monthly.reduce((best, m) => (m.room + m.danmaku > best.room + best.danmaku ? m : best), report.monthly[0]);
    section(false, (q, sy) => {
      let yy = chapter(q, sy, false, "CH.1", "CHAT LOG", "每个月，你说了多少话？",
        peak && peak.room + peak.danmaku ? `最热闹的是${peak.month.slice(0, 4)}年${Number(peak.month.slice(5))}月：${peak.room + peak.danmaku}句` : "这段时间还没有发言",
        `房间留言${fmt(t.room)}句，直播弹幕${fmt(t.danmaku)}句。`);
      return monthlyChart(q, yy + 24, report);
    });
    if (t.lives && t.attended) {
      section(false, (q, sy) => {
        const rate = Math.round(t.attended / t.lives * 100);
        let yy = chapter(q, sy, false, "CH.2", "ATTENDANCE", `${report.member_name}开播，你在不在？`,
          `到场率${rate}%`, `她开了${t.lives}场直播，你在${t.attended}场里发过弹幕。只要发过一条就算“在场”。`);
        return attendanceChart(q, yy + 24, report);
      });
    }
    if (Object.keys(report.delay).length) {
      const years = Object.keys(report.delay);
      const medians = years.map((year) => (years.length > 1 ? `${year}年` : "") + `${report.delay[year].median_minutes}分钟`).join("，");
      section(true, (q, sy) => {
        let yy = chapter(q, sy, true, "CH.3", "ON TIME", "开播后多久到？",
          `一半的场次在开播后${medians}内就到`, "按你在每场直播发出第一条弹幕的时间算。");
        return delaySection(q, yy, report);
      });
    }
    section(false, (q, sy) => {
      const streak = report.longest_streak;
      let yy = chapter(q, sy, false, "CH.4", "CHECK-IN", "打卡日历",
        streak.days ? `最长连续${streak.days}天没断过` : "", streak.days ? `${chinaDate(streak.start)}—${chinaDate(streak.end)}（日历下方黑线）。每格是一天，颜色越深那天说得越多。` : "");
      report.years.forEach((year) => { yy = calendar(q, yy + 28, report, year); });
      ["0", "1—4", "5—14", "15—39", "40+"].forEach((label, index) => {
        const lx = PAD + index * 112;
        rect(q, lx, yy + 14, 16, 16, HEAT[index][1]);
        text(q, label, lx + 24, yy + 8, 24, C.inkMuted);
      });
      return yy + 48;
    });
    section(true, (q, sy) => {
      const hours = report.hours;
      let peakHour = 0;
      hours.forEach((value, hour) => { if (value > hours[peakHour]) peakHour = hour; });
      let yy = chapter(q, sy, true, "CH.5", "TIME OF DAY", "你几点在说话？", `最常在${peakHour}点`, "按北京时间统计，包含房间留言和弹幕。");
      return hoursChart(q, yy + 24, report);
    });
    if (Object.keys(report.phrases).length) {
      section(true, (q, sy) => {
        let yy = chapter(q, sy, true, "CH.6", "CATCHPHRASE", "你的口头禅", "", "");
        return phraseSection(q, yy, report);
      });
    }
    if (report.nicknames.length) {
      section(false, (q, sy) => {
        let yy = chapter(q, sy, false, "CH.7", "NAME HISTORY", "昵称进化史", `用过${report.nicknames.length}个昵称`, "句数包含房间留言和弹幕。");
        return nicknameSection(q, yy, report);
      });
    }
    if (report.top_lives.length) {
      section(true, (q, sy) => {
        let yy = chapter(q, sy, true, "CH.8", "BEST LIVES", "直播间名场面", `一场最多刷了${report.top_lives[0].count}句`, "按单场弹幕数排的前三名，各摘几句。");
        return topLivesSection(q, yy, report);
      });
    }
    if (report.other_rooms.length || report.small_rooms.rooms) {
      section(true, (q, sy) => {
        const visited = report.other_rooms.reduce((sum, room) => sum + room.count, 0) + report.small_rooms.messages;
        let yy = chapter(q, sy, true, "CH.9", "VISITING", "在别人的房间", `串门${visited}句`, "只统计房间留言。");
        return roomsSection(q, yy + 16, report);
      });
    }
    if (dark === false) { y = divider(p, y, C.cream, C.navy); }
    y = footer(p, y, report);
    return y;
  };

  const loadFont = async () => {
    if (document.fonts && [...document.fonts].some((face) => face.family === FONT && face.status === "loaded")) return;
    const face = new FontFace(FONT, `url(${document.querySelector("script[src*='report.js']").src.replace(/report\.js.*$/, "fusion-pixel-zh_hans.woff2")})`);
    await face.load();
    document.fonts.add(face);
  };

  const renderPoster = async (canvas, report) => {
    await loadFont();
    const measureCanvas = document.createElement("canvas");
    const height = Math.ceil(layout({ ctx: measureCanvas.getContext("2d"), draw: false }, report));
    canvas.width = W * SCALE;
    canvas.height = height * SCALE;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.scale(SCALE, SCALE);
    layout({ ctx, draw: true }, report);
  };

  // ---------- page wiring ----------
  const statusBox = document.querySelector("#report-status");
  const output = document.querySelector("#report-output");
  const canvas = document.querySelector("#report-canvas");
  const form = document.querySelector("#report-form");
  const submit = document.querySelector("#report-submit");
  let currentJob = null;
  let currentReport = null;

  const showStatus = (message, isError = false) => {
    statusBox.hidden = false;
    statusBox.textContent = message;
    statusBox.classList.toggle("is-error", isError);
  };

  const errorMessage = (body, fallback) => (body && body.error && body.error.message) || (body && body.detail ? "输入有误，请检查 UID 和昵称" : fallback);

  const describe = (job) => {
    if (job.status === "queued") return `排队中，前面还有${Math.max(0, job.queue_position - 1)}位。`;
    if (job.status === "running") return `${job.progress}……第一次查询某位成员需要下载回放弹幕，可能要几分钟。`;
    return job.progress;
  };

  const poll = async (jobId) => {
    currentJob = jobId;
    for (;;) {
      if (currentJob !== jobId) return;
      let job;
      try {
        const response = await fetch(`/api/reports/${jobId}`, { headers: { Accept: "application/json" } });
        job = await response.json();
        if (!response.ok) { showStatus(errorMessage(job, "报告不存在或已过期"), true); if (submit) submit.disabled = false; return; }
      } catch {
        showStatus("网络有点问题，正在重试……", true);
        await new Promise((resolve) => setTimeout(resolve, 4000));
        continue;
      }
      if (job.status === "completed") {
        showStatus("生成好了！可以下载 PNG，或者复制链接分享（7 天内有效）。");
        currentReport = job.result;
        output.hidden = false;
        await renderPoster(canvas, job.result);
        if (submit) submit.disabled = false;
        return;
      }
      if (job.status === "failed") {
        showStatus(job.error ? job.error.message : "生成失败，请稍后再试", true);
        if (submit) submit.disabled = false;
        return;
      }
      showStatus(describe(job));
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
  };

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const uid = form.querySelector("#report-uid").value.trim();
    if (!/^[0-9]{1,12}$/.test(uid)) { showStatus("UID 应该是 1 到 12 位数字。", true); return; }
    const nicknames = form.querySelector("#report-nicknames").value.split("\n").map((line) => line.trim()).filter(Boolean).slice(0, 5);
    submit.disabled = true;
    output.hidden = true;
    showStatus("正在提交……");
    try {
      const response = await fetch("/api/reports", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""
        },
        body: JSON.stringify({
          uid,
          member_pocket_id: form.querySelector("#report-member").value,
          scope: form.querySelector("input[name='scope']:checked").value,
          extra_nicknames: nicknames
        })
      });
      const job = await response.json();
      if (!response.ok) { showStatus(errorMessage(job, "提交失败，请检查输入"), true); submit.disabled = false; return; }
      history.replaceState(null, "", `/report?job=${job.id}`);
      poll(job.id);
    } catch {
      showStatus("提交失败，请稍后再试", true);
      submit.disabled = false;
    }
  });

  document.querySelector("#report-download")?.addEventListener("click", () => {
    if (!currentReport) return;
    canvas.toBlob((blob) => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `口袋48年度报告-${currentReport.display_name || "我"}-${currentReport.scope}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    }, "image/png");
  });

  document.querySelector("#report-copy-link")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      showStatus("链接已复制，7 天内有效。");
    } catch {
      showStatus(window.location.href);
    }
  });

  const initialJob = new URLSearchParams(window.location.search).get("job");
  if (initialJob && /^[0-9a-f]{32}$/.test(initialJob)) {
    if (submit) submit.disabled = true;
    showStatus("正在读取报告……");
    poll(initialJob);
  }

  window.P48Report = { renderPoster };
})();
