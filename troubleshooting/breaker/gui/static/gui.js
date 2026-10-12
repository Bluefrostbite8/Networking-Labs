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

  // Confirm before actions that reveal answers, cost points or delete a lab.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => { if (!confirm(form.dataset.confirm)) e.preventDefault(); });
  });

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
