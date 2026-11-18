/* The Explore page: a working model of the dashboard, drawn from sample data (explorer-data.js). */
(function () {
  "use strict";

  const OK = 0, WARN = 1, ERROR = 2, STALE = 3;
  const ICONS = ["✓", "▲", "✗", "■"];
  const LABELS = ["OK", "WARN", "ERROR", "STALE"];
  const VERDICTS = ["NOMINAL", "DEGRADED", "FAULT", "STALE"];
  const HINT = [["j/k move", "keys"], ["tab/←→ pane", "keys"], ["/ search", "keys"], ["n problem", "keys"],
    ["a actions", "actions"], ["p pause", "keys"], ["o options", "options"], ["? help", "keys"], ["q quit", "keys"]];
  const HELP = [["j / k / arrows", "move the cursor, the other panes follow"], ["tab / ← / → / enter", "switch between categories and topics"],
    ["/", "search all topics"], ["esc", "focus categories, or clear the search"], ["n / N", "next or previous topic with a problem"],
    ["s", "sort worst first or by name"], ["a", "quick actions on this topic"], ["p", "pause the display"],
    ["r", "reset this page's changes"], ["?", "close this help"]];

  const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fit = (text, width) => { const s = String(text); return s.length > width ? "…" + s.slice(s.length - width + 1) : s.padEnd(width); };
  const cell = (text, width, cls, tip, extra) =>
    `<span class="${cls || ""}" style="width:${width}ch"${tip ? ` data-tip="${esc(tip)}"` : ""}${extra || ""}>${esc(fit(text, width))}</span>`;

  /** The page a code is documented on, from its prefix. */
  function codeHref(root, code) {
    const page = window.TESTUDO_EXPLORER.codePages[code.split("-")[0]];
    return `${root}${page}/#${code.toLowerCase()}`;
  }

  class Explorer {
    constructor(host) {
      this.host = host;
      this.root = host.dataset.root;
      this.data = JSON.parse(JSON.stringify(window.TESTUDO_EXPLORER.categories));
      this.reset();
      this.startedAt = Date.now();
      host.innerHTML = '<div class="tx-term" tabindex="0" role="application" aria-label="A working model of the Testudo dashboard. Click here, then use the keys it lists."></div>' +
        '<div class="tx-float" hidden></div><div class="tx-inspect" aria-live="polite"></div>';
      this.term = host.querySelector(".tx-term");
      this.float = host.querySelector(".tx-float");
      this.inspect = host.querySelector(".tx-inspect");
      this.term.addEventListener("keydown", (event) => this.onKey(event));
      this.term.addEventListener("click", (event) => this.onClick(event));
      this.term.addEventListener("mousemove", (event) => this.onHover(event));
      this.term.addEventListener("mouseleave", () => { this.float.hidden = true; });
      this.showTip("start");
      this.render();
      this.timer = setInterval(() => { if (!document.body.contains(host)) clearInterval(this.timer); else if (!this.paused) this.render(); }, 1000);
    }

    reset() {
      this.overrides = {};
      this.catIndex = 0;
      this.topicIndex = 0;
      this.pane = "categories";
      this.byName = false;
      this.paused = false;
      this.mode = "normal";
      this.query = "";
      this.note = "";
    }

    severity(topic) {
      const codes = this.codes(topic);
      return codes.length ? Math.max(...codes.map((c) => c.sev)) : topic.sev || OK;
    }

    /** The topic's codes, with what quick actions changed on this page applied. */
    codes(topic) {
      return (topic.codes || [])
        .map((c) => ({ ...c, sev: c.code in this.overrides ? this.overrides[c.code] : c.sev }))
        .filter((c) => c.sev !== null);
    }

    categories() {
      const q = this.query.toLowerCase();
      const shown = this.data
        .map((cat) => ({ ...cat, topics: !q || cat.name.toLowerCase().includes(q) ? cat.topics : cat.topics.filter((t) => t.name.toLowerCase().includes(q)) }))
        .filter((cat) => cat.topics.length);
      const worst = (cat) => Math.max(...cat.topics.map((t) => this.severity(t)));
      const regular = shown.filter((cat) => !cat.last).sort((a, b) => (this.byName ? 0 : worst(b) - worst(a)) || a.name.localeCompare(b.name));
      return regular.concat(shown.filter((cat) => cat.last));
    }

    topics(cat) {
      return [...cat.topics].sort((a, b) => (this.byName ? 0 : this.severity(b) - this.severity(a)) || a.name.localeCompare(b.name));
    }

    current() {
      const cats = this.categories();
      this.catIndex = Math.min(this.catIndex, Math.max(cats.length - 1, 0));
      const cat = cats[this.catIndex];
      const topics = cat ? this.topics(cat) : [];
      this.topicIndex = Math.min(this.topicIndex, Math.max(topics.length - 1, 0));
      return { cats, cat, topics, topic: topics[this.topicIndex] };
    }

    /** Every active code, worst first, one entry per code. */
    problems() {
      const byCode = new Map();
      for (const cat of this.data) {
        if (cat.last) continue;
        for (const topic of cat.topics) {
          for (const c of this.codes(topic)) {
            if (c.sev === OK) continue;
            const entry = byCode.get(c.code) || { code: c.code, sev: c.sev, topics: [] };
            entry.sev = Math.max(entry.sev, c.sev);
            entry.topics.push(topic.name);
            byCode.set(c.code, entry);
          }
        }
      }
      return [...byCode.values()].sort((a, b) => b.sev - a.sev || a.code.localeCompare(b.code));
    }

    select(topicName) {
      this.query = "";
      this.locate(topicName);
    }

    /** Runs `change`, which may reorder the rows, with the cursor staying on the same topic. */
    keepingCursor(change) {
      const here = this.current().topic;
      change();
      if (here) this.locate(here.name);
    }

    locate(topicName) {
      const cats = this.categories();
      cats.forEach((cat, i) => this.topics(cat).forEach((t, j) => {
        if (t.name === topicName) { this.catIndex = i; this.topicIndex = j; }
      }));
    }

    jump(step) {
      const names = [...new Set(this.problems().flatMap((p) => p.topics))];
      if (!names.length) return;
      const here = this.current().topic;
      const at = here ? names.indexOf(here.name) : -1;
      this.select(names[(at + step + names.length) % names.length]);
      this.pane = "topics";
    }

    move(step) {
      const { cats, topics } = this.current();
      if (this.pane === "categories") {
        this.catIndex = Math.max(0, Math.min(cats.length - 1, this.catIndex + step));
        this.topicIndex = 0;
      } else {
        this.topicIndex = Math.max(0, Math.min(topics.length - 1, this.topicIndex + step));
      }
    }

    onKey(event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key;
      if (key === "Tab" && this.mode === "normal" && event.shiftKey && this.pane === "categories") return; // Leave the model backwards.
      event.stopPropagation(); // The docs site has its own shortcuts for these keys.
      if (this.handle(key)) { event.preventDefault(); this.render(); }
    }

    handle(key) {
      if (this.mode === "search") return this.handleSearch(key);
      if (this.mode === "actions") return this.handleActions(key);
      if (this.mode === "help") { if (key === "?" || key === "Escape") this.mode = "normal"; return true; }
      this.note = "";
      switch (key) {
        case "j": case "ArrowDown": this.move(1); return true;
        case "k": case "ArrowUp": this.move(-1); return true;
        case "Tab": case "Enter": case "ArrowLeft": case "ArrowRight":
          this.pane = this.pane === "categories" ? "topics" : "categories"; return true;
        case "Escape": if (this.query) this.query = ""; this.pane = "categories"; return true;
        case "n": this.jump(1); return true;
        case "N": this.jump(-1); return true;
        case "s": this.keepingCursor(() => { this.byName = !this.byName; }); return true;
        case "p": this.paused = !this.paused; return true;
        case "r": this.keepingCursor(() => { this.overrides = {}; }); this.note = "Changes undone"; return true;
        case "/": this.mode = "search"; this.query = ""; return true;
        case "?": this.mode = "help"; return true;
        case "a": this.openActions(); return true;
        case "o": this.showTip("options"); return true;
        case "y": this.note = "Copied the topic name in the real dashboard"; return true;
        case "q": this.note = "In the real dashboard, q quits"; return true;
        default: return false;
      }
    }

    handleSearch(key) {
      if (key === "Enter") this.mode = "normal";
      else if (key === "Escape") { this.mode = "normal"; this.query = ""; }
      else if (key === "Backspace") this.query = this.query.slice(0, -1);
      else if (key.length === 1) this.query += key;
      else return false;
      this.catIndex = 0;
      this.topicIndex = 0;
      return true;
    }

    openActions() {
      const { topic } = this.current();
      this.actionCodes = topic ? (topic.codes || []).map((c) => c.code) : [];
      this.actionIndex = 0;
      if (this.actionCodes.length) this.mode = "actions";
      else this.note = "This topic has no code to act on";
    }

    handleActions(key) {
      const code = this.actionCodes[this.actionIndex];
      const done = (note) => { this.mode = "normal"; this.note = note; return true; };
      switch (key) {
        case "w": this.keepingCursor(() => { this.overrides[code] = WARN; }); return done(`${code} now counts as WARN. The real dashboard saves this to your config`);
        case "m": this.keepingCursor(() => { this.overrides[code] = null; }); return done(`${code} muted. The real dashboard saves this to your config`);
        case "r": this.keepingCursor(() => { delete this.overrides[code]; }); return done(`${code} restored`);
        case "c": this.actionIndex = (this.actionIndex + 1) % this.actionCodes.length; return true;
        case "Escape": this.mode = "normal"; return true;
        default: return false;
      }
    }

    onClick(event) {
      const row = event.target.closest("[data-cat],[data-topic],[data-goto]");
      const tip = event.target.closest("[data-tip]");
      if (tip) this.showTip(tip.dataset.tip);
      if (!row) return;
      if (row.dataset.cat !== undefined) { this.catIndex = Number(row.dataset.cat); this.topicIndex = 0; this.pane = "categories"; }
      else if (row.dataset.topic !== undefined) { this.topicIndex = Number(row.dataset.topic); this.pane = "topics"; }
      else { this.select(row.dataset.goto); this.pane = "topics"; }
      this.render();
    }

    onHover(event) {
      const tip = event.target.closest("[data-tip]");
      if (!tip) { this.float.hidden = true; return; }
      const info = this.tipInfo(tip.dataset.tip);
      this.float.textContent = info.title;
      this.float.hidden = false;
      const box = this.host.getBoundingClientRect();
      const x = Math.min(event.clientX - box.left + 12, box.width - this.float.offsetWidth - 4);
      this.float.style.left = `${Math.max(0, x)}px`;
      this.float.style.top = `${event.clientY - box.top + 16}px`;
      this.showTip(tip.dataset.tip);
    }

    /** A tip is a key of `tips`, or `code:IMG-002` / `value:<text>|<page>` built on the spot. */
    tipInfo(key) {
      const tips = window.TESTUDO_EXPLORER.tips;
      if (key.startsWith("code:")) {
        const code = key.slice(5);
        return { title: code, text: "One specific problem. Its entry gives the likely causes and what to do.", href: codeHref(this.root, code), link: `Read ${code}` };
      }
      if (key.startsWith("value:")) {
        const [name, page] = key.slice(6).split("|");
        return { title: name, text: "A reading from the plugin that checks this topic. Each plugin's page explains its own.", href: this.root + page + "/", link: "Open the plugin's page" };
      }
      if (key.startsWith("cat:")) {
        const [name, page] = key.slice(4).split("|");
        return { title: name, text: "Topics grouped by what they are. Move the cursor here and the Topic Panel follows.", href: page ? this.root + page + "/" : null, link: `Read about ${name}` };
      }
      const tip = tips[key] || tips.start;
      return { ...tip, href: tip.page ? this.root + tip.page : null, link: tip.link };
    }

    showTip(key) {
      if (key === this.lastTip) return;
      this.lastTip = key;
      const info = this.tipInfo(key);
      const link = info.href ? ` <a href="${esc(info.href)}">${esc(info.link)} →</a>` : "";
      this.inspect.innerHTML = `<strong>${esc(info.title)}</strong> ${esc(info.text)}${link}`;
    }

    statusBar(cats) {
      const topics = this.data.filter((c) => !c.nodes && !c.last).flatMap((c) => c.topics);
      const counts = [0, 0, 0, 0];
      topics.forEach((t) => { counts[this.severity(t)] += 1; });
      const worst = counts[ERROR] ? ERROR : counts[STALE] ? STALE : counts[WARN] ? WARN : OK;
      const score = topics.reduce((sum, t) => sum + this.severity(t), 0) / topics.length;
      const health = Math.max(0, Math.min(1, 1 - score / ERROR));
      const filled = Math.round(health * 10);
      const up = Math.floor((Date.now() - this.startedAt) / 1000);
      const uptime = up >= 60 ? `${Math.floor(up / 60)}m${String(up % 60).padStart(2, "0")}s` : `${up}s`;
      const left = `<b data-tip="brand">testudo</b>  <span data-tip="distro"><i>ROS</i> jazzy</span> │ <span data-tip="domain"><i>domain</i> 42</span> │ <span data-tip="rmw">rmw_cyclonedds_cpp</span> │ <span data-tip="uptime"><i>up</i> ${uptime}</span>${this.paused ? ' <b class="tx-paused" data-tip="paused"> PAUSED </b>' : ""}`;
      const parts = [STALE, ERROR, WARN].filter((s) => counts[s]).map((s) => `${ICONS[s]}${counts[s]}`).join(" ");
      const right = `<b class="tx-chip tx-chip-${worst}" data-tip="verdict"> ${ICONS[worst]} ${VERDICTS[worst]} </b> <span data-tip="counts">${parts}</span>  <span data-tip="gauge"><i>weighted</i> ${"█".repeat(filled)}<i>${"░".repeat(10 - filled)}</i> ${Math.round(health * 100)}%</span>`;
      return `<div class="tx-bar"><span>${left}</span><span>${right}</span></div>`;
    }

    breakdown(cat) {
      if (cat.last === "excluded") return `<i>${cat.topics.length} topic${cat.topics.length === 1 ? "" : "s"}</i>`;
      const counts = [0, 0, 0, 0];
      cat.topics.forEach((t) => { counts[this.severity(t)] += 1; });
      return [STALE, ERROR, WARN, OK].filter((s) => counts[s]).map((s) => `<span class="tx-s${s}">${ICONS[s]}${counts[s]}</span>`).join(" ");
    }

    leftPane(cats) {
      const marker = this.byName ? ["Category ▲", "Status"] : ["Category", "Status ▼"];
      const rows = cats.map((cat, i) => {
        const on = i === this.catIndex ? (this.pane === "categories" ? " tx-on-cat" : " tx-on-cat tx-dim-sel") : "";
        return `<div class="tx-row${on}" data-cat="${i}">${cell(" " + cat.name, 20, "", `cat:${cat.name}|${cat.page || ""}`)}<span data-tip="breakdown">${this.breakdown(cat)}</span></div>`;
      }).join("");
      const problems = this.problems();
      const attention = problems.slice(0, 5).map((p) => {
        const where = p.topics.length > 1 ? `${p.topics.length} topics` : p.topics[0];
        return `<div class="tx-row tx-link" data-goto="${esc(p.topics[0])}"> <span class="tx-s${p.sev}" data-tip="code:${p.code}">${ICONS[p.sev]} ${p.code}</span> <i data-tip="where">${esc(fit(where, 28 - p.code.length - 3).trimEnd())}</i></div>`;
      }).join("");
      const more = problems.length > 5 ? `<div class="tx-row" data-tip="more"> <i>+${problems.length - 5} more</i></div>` : "";
      const cleared = window.TESTUDO_EXPLORER.cleared.map((c) =>
        `<div class="tx-row tx-faded"> <span data-tip="code:${c.code}">${ICONS[c.sev]} ${c.code} ${c.ago}</span> <span data-tip="cleared">${esc(c.topic)}</span></div>`).join("");
      return `<div class="tx-left"><div class="tx-head">${cell(" " + marker[0], 20, "")}<span data-tip="sort">${marker[1]}</span></div>${rows}` +
        (problems.length ? `<div class="tx-row">&nbsp;</div><div class="tx-row" data-tip="attention"><b> Needs attention</b></div>${attention}${more}` : "") +
        `<div class="tx-row">&nbsp;</div><div class="tx-row tx-faded" data-tip="cleared"><b> Cleared</b></div>${cleared}</div>`;
    }

    statusCell(topic) {
      const sev = this.severity(topic);
      if (sev === OK && topic.quiet) return { text: topic.quiet, cls: topic.quiet.startsWith("●") ? "tx-s0" : "tx-mute", tip: "quiet" };
      if (topic.excluded) return { text: "", cls: "", tip: "excluded" };
      return { text: `${ICONS[sev]} ${LABELS[sev]}`, cls: `tx-s${sev}`, tip: "status" };
    }

    rightPane(cat, topics) {
      if (!cat) return '<div class="tx-right"><div class="tx-head"> Topic</div><div class="tx-row"> <i>No topic matches the search</i></div></div>';
      const rateName = cat.rate || "Hz";
      const head = `<div class="tx-head">${cell(" Topic", 38, "", "topic")}${cell("Status", 11, "", "status")}${cell(rateName, 8, "", cat.rate === "State" ? "state" : cat.rate === "Mem" ? "mem" : "hz")}${cell(cat.column || "", 10, "", cat.column ? "column" : null)}</div>`;
      const rows = topics.map((topic, i) => {
        const on = i === this.topicIndex ? (this.pane === "topics" ? " tx-on-topic" : " tx-on-topic tx-dim-sel") : "";
        const status = this.statusCell(topic);
        return `<div class="tx-row${on}" data-topic="${i}">${cell(" " + topic.name, 38, "", "topic")}${cell(status.text, 11, status.cls, status.tip)}${cell(topic.rate ?? "", 8, "", "hz")}${cell(topic.extra ?? "", 10, "", cat.column ? "column" : null)}</div>`;
      }).join("");
      return `<div class="tx-right">${head}${rows}</div>`;
    }

    detail(cat, topic) {
      if (!topic) return '<div class="tx-detail"></div>';
      const sev = this.severity(topic);
      const badge = topic.excluded ? "" : `<b class="tx-s${sev}" data-tip="dsev">${ICONS[sev]} ${LABELS[sev]}</b>  `;
      const head = `<div class="tx-row">${badge}<b data-tip="dtopic">${esc(topic.name)}</b>  <i data-tip="dtype">${esc(topic.type)}</i> <i>·</i> <i data-tip="dtier">${esc(topic.tier)} tier</i></div>`;
      const page = cat.page || "concepts/tiers";
      const values = (topic.values || []).map(([key, value, cls]) =>
        `<div class="tx-row" data-tip="value:${esc(key)}|${page}"><i>${esc(key.padEnd(15))}</i> <span class="${cls || ""}">${esc(value)}</span></div>`).join("");
      const codes = this.codes(topic).map((c) =>
        `<div class="tx-row" data-tip="code:${c.code}"><b>[${c.code}]</b> <span class="tx-s${c.sev}">${esc(c.msg)}</span></div>`).join("");
      const past = (topic.past || []).map((line) => `<div class="tx-row tx-faded" data-tip="cleared">${esc(line)}</div>`).join("");
      return `<div class="tx-detail">${head}${values}${codes ? '<div class="tx-row">&nbsp;</div>' + codes : ""}${past ? '<div class="tx-row">&nbsp;</div>' + past : ""}</div>`;
    }

    footer() {
      if (this.mode === "search") return `<div class="tx-search">/ ${esc(this.query)}<span class="tx-caret">█</span></div>`;
      if (this.mode === "actions") {
        const code = this.actionCodes[this.actionIndex];
        const next = this.actionCodes.length > 1 ? " · c next code" : "";
        return `<div class="tx-actions" data-tip="actions"><b>${code}</b>  w warn · m mute · r restore${next} · esc cancel</div>`;
      }
      if (this.note) return `<div class="tx-hint">${esc(this.note)}</div>`;
      const entries = HINT.map(([text, tip]) => `<span data-tip="${tip}">${esc(text)}</span>`).join(" · ");
      return `<div class="tx-hint">${entries}</div>`;
    }

    helpOverlay() {
      if (this.mode !== "help") return "";
      const rows = HELP.map(([key, text]) => `<div><b>${esc(key.padEnd(22))}</b>${esc(text)}</div>`).join("");
      return `<div class="tx-help"><div><b>Keys on this page</b></div><div>&nbsp;</div>${rows}</div>`;
    }

    render() {
      const { cats, cat, topics, topic } = this.current();
      this.term.innerHTML = this.statusBar(cats) +
        `<div class="tx-panes">${this.leftPane(cats)}${this.rightPane(cat, topics)}</div>` +
        this.detail(cat || {}, topic) + this.footer() + this.helpOverlay();
    }
  }

  /** Material swaps pages without reloading, so each page view looks for the model again. */
  function mount() {
    const host = document.querySelector("#tx-explorer");
    if (host && !host.dataset.mounted) { host.dataset.mounted = "1"; new Explorer(host); }
  }
  if (window.document$) window.document$.subscribe(mount);
  else document.addEventListener("DOMContentLoaded", mount);
})();
