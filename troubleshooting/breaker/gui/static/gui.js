// Breaker console: live job logs, clocks, confirmations. No framework.
(function () {
  "use strict";

  function fmt(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
  }
  function parse(iso) { return iso ? new Date(iso).getTime() : null; }

  // Clocks: session clock (data-since) and job clocks (data-started / data-ended).
  function tickClocks() {
    const now = Date.now();
    document.querySelectorAll("[data-since]").forEach((el) => {
      el.textContent = fmt((now - parse(el.dataset.since)) / 1000);
    });
    document.querySelectorAll("[data-role=clock]").forEach((el) => {
      const start = parse(el.dataset.started), end = parse(el.dataset.ended);
      if (start) el.textContent = fmt(((end || now) - start) / 1000);
    });
  }
  tickClocks();
  setInterval(tickClocks, 1000);

  // Colour the lines that matter, like a terminal would.
  function classify(line) {
    if (/\b(FAIL|failed|error|Traceback|NOT FOUND|expected )/.test(line)) return "l-fail";
    if (/\b(PASS|as expected|ready in|healthy lab ready|graded Exact|Session score)\b/.test(line)) return "l-pass";
    if (/\b(warning|retry|will retry|took \d+ s|Temporary|interrupted)\b/i.test(line)) return "l-warn";
    return "";
  }
  function append(pre, text) {
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    const frag = document.createDocumentFragment();
    text.split(/(?<=\n)/).forEach((line) => {
      const span = document.createElement("span");
      const cls = classify(line);
      if (cls) span.className = cls;
      span.textContent = line;
      frag.appendChild(span);
    });
    pre.appendChild(frag);
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  }

  // Live console: poll the job log by byte offset until the job ends.
  document.querySelectorAll(".console[data-job]").forEach((box) => {
    const id = box.dataset.job, pre = box.querySelector("[data-role=log]");
    const statusEl = box.querySelector("[data-role=status]"), clock = box.querySelector("[data-role=clock]");
    let offset = 0, wasRunning = box.dataset.status === "running";
    async function poll() {
      try {
        const res = await fetch(`/api/jobs/${encodeURIComponent(id)}?offset=${offset}`);
        const data = await res.json();
        if (data.text) append(pre, data.text);
        offset = data.offset;
        if (data.progress) showProgress(id, data.progress);
        box.dataset.status = data.status;
        statusEl.textContent = data.status + (data.returncode && data.returncode !== 0 ? ` (exit ${data.returncode})` : "");
        if (data.ended) clock.dataset.ended = data.ended;
        if (data.status === "running") { setTimeout(poll, 1000); return; }
        const led = box.querySelector(".led");
        led.className = "led " + (data.status === "done" ? "led-green" : "led-magenta");
        const stop = box.querySelector("[data-role=stop]");
        if (stop) stop.remove();
        // A job that just finished changes the session state: reload once to show it.
        if (wasRunning && !box.closest(".tall")) setTimeout(() => location.reload(), 1500);
      } catch (e) {
        setTimeout(poll, 3000);
      }
    }
    poll();
    const stop = box.querySelector("[data-role=stop]");
    if (stop) stop.addEventListener("click", async () => {
      if (!confirm("Send Ctrl+C to this job? The CLI stops where it is and prints the clean-up command.")) return;
      await fetch(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: "POST", headers: { "X-Token": window.BREAKER_TOKEN } });
    });
  });

  // Start progress: the bar in the next-step card follows the job's own log.
  function showProgress(id, p) {
    document.querySelectorAll(`[data-progress-for="${CSS.escape(id)}"]`).forEach((box) => {
      const bar = box.querySelector(".bar"), fill = bar.querySelector("span");
      fill.style.width = p.pct + "%";
      bar.setAttribute("aria-valuenow", p.pct);
      box.querySelector("[data-role=stage]").textContent = p.stage;
      box.querySelector("[data-role=pct]").textContent = p.pct + "%";
      box.classList.toggle("is-failed", !!p.failed);
      if (p.ready) document.title = "Ready · " + document.title.replace(/^Ready · /, "");
    });
  }

  // ---------------------------------------------------------------- CML panel
  // One request feeds every panel on the page (rail meter, home panel). The
  // server reads CML in the background, so this never blocks a page load.
  const cmlBoxes = document.querySelectorAll("[data-cml]");
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function cleanForm(incident, label, confirmText) {
    const f = el("form");
    f.method = "post"; f.action = window.BREAKER_CLEAN_URL;
    f.dataset.confirm = confirmText;
    [["token", window.BREAKER_TOKEN], ["incident", incident], ["back", window.BREAKER_PATH]].forEach(([n, v]) => {
      const i = el("input"); i.type = "hidden"; i.name = n; i.value = v; f.appendChild(i);
    });
    const b = el("button", "btn btn-danger btn-small", label);
    if (window.BREAKER_BUSY) b.disabled = true;
    f.appendChild(b);
    wireConfirm(f);
    return f;
  }
  function renderMeter(box, st) {
    const meter = box.querySelector("[data-role=meter]");
    const fill = meter.querySelector(".meter-bar span"), text = meter.querySelector("[data-role=meter-text]");
    meter.classList.remove("tone-green", "tone-amber", "tone-magenta", "tone-muted");
    if (st.pending) { text.textContent = "Reading CML…"; return; }
    if (!st.ok) { meter.classList.add("tone-magenta"); fill.style.width = "0%"; text.textContent = "CML unreachable"; return; }
    const cap = st.cap, used = st.running || 0;
    const pct = cap ? Math.min(100, Math.round((100 * used) / cap)) : 0;
    fill.style.width = pct + "%";
    const tone = !cap ? "tone-muted" : used >= cap ? "tone-magenta" : used / cap >= 0.6 ? "tone-amber" : "tone-green";
    meter.classList.add(tone);
    text.textContent = cap ? `${used} of ${cap} nodes running` + (cap - used > 0 ? ` · ${cap - used} free` : " · full")
                           : `${used} nodes running (set node_cap in config.yaml)`;
  }
  function renderLabs(box, st) {
    const host = box.querySelector("[data-role=cml-labs]");
    if (!host) return;
    host.replaceChildren();
    if (st.pending) { host.appendChild(el("p", "muted small", "Reading the labs on the server…")); return; }
    if (!st.ok) {
      const p = el("div", "cml-error");
      p.appendChild(el("p", "", "Can't reach CML."));
      p.appendChild(el("p", "mono small", st.error || ""));
      p.appendChild(el("p", "muted small", "Is the CML VM running, and was this console started from a shell that has VIRL2_URL, VIRL2_USER, VIRL2_PASS and CML_CA_BUNDLE set?"));
      host.appendChild(p);
      return;
    }
    if (!st.labs.length) { host.appendChild(el("p", "muted small", "No labs on the server. Everything is free.")); return; }
    const list = el("ul", "cml-labs");
    let incidents = 0;
    st.labs.forEach((lab) => {
      const li = el("li", "cml-lab" + (lab.leftover ? " is-leftover" : ""));
      const main = el("div", "cml-lab-main");
      const top = el("p", "cml-lab-title");
      top.appendChild(el("span", "led " + (lab.running ? "led-green" : "led-muted")));
      if (lab.session) {
        const a = el("a", "mono", lab.incident); a.href = "/s/" + encodeURIComponent(lab.session); top.appendChild(a);
      } else {
        top.appendChild(el("span", lab.incident ? "mono" : "", lab.incident || lab.title));
      }
      main.appendChild(top);
      const kinds = { session: "incident lab", selftest: "selftest lab", healthy: "healthy run", other: "not managed by Breaker" };
      const bits = [lab.running ? `${lab.running} of ${lab.nodes} nodes running` : `stopped, ${lab.nodes} nodes`, kinds[lab.kind] || ""];
      if (lab.incident && lab.session_state) bits.push("session " + lab.session_state);
      if (lab.leftover) bits.push("leftover: safe to delete");
      main.appendChild(el("p", "muted small", bits.filter(Boolean).join(" · ")));
      li.appendChild(main);
      if (lab.incident) {
        incidents += 1;
        li.appendChild(cleanForm(lab.incident, "Delete",
          `Stop, wipe and delete ${lab.title}? Only this incident lab is touched; session files stay.`));
      }
      list.appendChild(li);
    });
    host.appendChild(list);
    if (st.busy) host.appendChild(el("p", "muted small", `Delete buttons unlock when "${st.busy}" finishes.`));
    const all = box.querySelector("[data-role=clean-all]");
    if (all) all.hidden = incidents < 2;
  }
  async function loadCml(refresh) {
    if (!cmlBoxes.length) return;
    cmlBoxes.forEach((b) => b.classList.add("is-loading"));
    try {
      const res = await fetch("/api/cml" + (refresh ? "?refresh=1" : ""));
      const st = await res.json();
      cmlBoxes.forEach((b) => { renderMeter(b, st); if (b.dataset.full === "1") renderLabs(b, st); });
      if (st.stale || st.pending) setTimeout(() => loadCml(false), 4000);
    } catch (e) {
      cmlBoxes.forEach((b) => renderMeter(b, { ok: false }));
    } finally {
      cmlBoxes.forEach((b) => b.classList.remove("is-loading"));
    }
  }
  loadCml(false);
  setInterval(() => { if (!document.hidden) loadCml(false); }, 30000);
  document.querySelectorAll("[data-role=cml-refresh]").forEach((b) => b.addEventListener("click", () => loadCml(true)));

  // Flash messages can be dismissed.
  document.querySelectorAll("[data-role=dismiss]").forEach((b) => b.addEventListener("click", () => b.parentElement.remove()));

  // Notes keep a local draft until saved, so a reload or a misclick never loses them.
  document.querySelectorAll("form[data-draft]").forEach((form) => {
    const key = "breaker-draft-" + form.dataset.draft, area = form.querySelector("textarea");
    const state = form.querySelector("[data-role=draft-state]");
    let saved = area.value, draft = null;
    try { draft = localStorage.getItem(key); } catch (e) { /* storage blocked: no drafts */ }
    if (draft !== null && draft !== saved) {
      const restore = el("button", "btn btn-quiet btn-small", "Restore unsaved draft");
      restore.type = "button";
      restore.addEventListener("click", () => { area.value = draft; restore.remove(); state.textContent = "Draft restored, not saved yet"; });
      state.replaceChildren(restore);
    }
    area.addEventListener("input", () => {
      try { localStorage.setItem(key, area.value); state.textContent = "Draft kept in this browser"; } catch (e) { /* ignore */ }
    });
    form.addEventListener("submit", () => { try { localStorage.removeItem(key); } catch (e) { /* ignore */ } });
  });

  // Confirm before actions that reveal answers, cost points or delete a lab.
  function wireConfirm(form) {
    form.addEventListener("submit", (e) => { if (!confirm(form.dataset.confirm)) e.preventDefault(); });
  }
  document.querySelectorAll("form[data-confirm]").forEach(wireConfirm);

  // Copy the publish commands.
  document.querySelectorAll("[data-role=copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pre = btn.parentElement.querySelector("[data-copy]");
      try { await navigator.clipboard.writeText(pre.textContent); btn.textContent = "Copied"; }
      catch (e) { btn.textContent = "Select and copy manually"; }
    });
  });

  // New incident: practice faults follow the chosen lab.
  const labs = document.querySelectorAll("input[name=lab]");
  function syncPractice() {
    const chosen = document.querySelector("input[name=lab]:checked");
    document.querySelectorAll(".practice").forEach((p) => {
      const on = chosen && p.dataset.lab === chosen.value;
      p.hidden = !on;
      p.querySelectorAll("input").forEach((i) => { i.disabled = !on; if (!on) i.checked = false; });
    });
  }
  labs.forEach((r) => r.addEventListener("change", syncPractice));
  if (labs.length) syncPractice();
})();
