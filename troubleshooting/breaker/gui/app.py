"""Flask app: a local console over the breaker CLI (build step 6).

Run from WSL with `python -m breaker gui`, then open http://localhost:5050 in
the Windows browser (WSL1 shares the Windows network stack). It listens on
127.0.0.1 only, and every POST carries a per-run token so no other web page can
press its buttons.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from ..lab_model import REPO_ROOT, LabModelError
from ..report import TIER_NAMES
from . import data, jobs
from .render import render_file

HERE = Path(__file__).resolve().parent


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    app.secret_key = secrets.token_hex(16)
    token = secrets.token_hex(16)

    @app.context_processor
    def globals_():
        return {"token": token, "running": jobs.running_cml_job(), "tiers": TIER_NAMES,
                "states": data.STATES, "now": datetime.now()}

    @app.before_request
    def check_token():
        if request.method == "POST" and request.form.get("token", request.headers.get("X-Token")) != token:
            abort(403)

    def job_or_flash(args: list[str], title: str, target: str | None, back: str):
        try:
            jobs.start(args, title, target)
        except jobs.Busy as exc:
            flash(str(exc), "error")
        return redirect(back)

    # ---------------------------------------------------------------- pages

    @app.get("/")
    def index():
        return render_template("index.html", sessions=data.sessions(), labs=data.labs(),
                               recent=jobs.all_jobs(8))

    @app.get("/new")
    def new():
        labs = [lab for lab in data.labs() if lab["profile"]]
        return render_template("new.html", labs=labs)

    @app.post("/new")
    def create():
        lab = request.form.get("lab", "")
        tier = request.form.get("tier", "2")
        args = ["break", lab, "--tier", tier]
        if request.form.get("seed", "").strip():
            args += ["--seed", request.form["seed"].strip()]
        for fid in request.form.getlist("fault"):
            args += ["--fault", fid]
        code, out = jobs.run_now(args)
        m = re.search(r"^Session (\S+):", out, re.M)
        if code != 0 or not m:
            flash(out.strip() or "break failed", "error")
            return redirect(url_for("new"))
        flash(out.strip().splitlines()[0], "ok")
        return redirect(url_for("session", name=m.group(1)))

    @app.get("/s/<name>")
    def session(name: str):
        s = data.find_session(name)
        if not s:
            abort(404)
        view = request.args.get("view") or ("debrief" if s["has_debrief"] and s["state"] == "debriefed" else "ticket")
        if view == "debrief" and s["state"] != "debriefed":
            view = "ticket"
        doc = s["path"] / f"{view}.md"
        body = render_file(doc) if doc.exists() else ""
        return render_template("session.html", s=s, view=view, body=body, job=jobs.latest_for(s["incident"]),
                               where=s["path"].relative_to(REPO_ROOT).as_posix())

    @app.get("/selftest")
    def selftest():
        labs = [lab for lab in data.labs() if lab["profile"]]
        # Default: the lab with faults left to test where the most are already proven
        # (its profile and quirks are the best known), then the most left to test.
        ready = [lab for lab in labs if lab["untested"]]
        best = max(ready, key=lambda lab: (len(lab["proven"]), lab["untested"]))["lab"] if ready else None
        return render_template("selftest.html", faults=data.fault_rows(), labs=labs, best_lab=best)

    @app.get("/jobs")
    def jobs_page():
        return render_template("jobs.html", jobs=jobs.all_jobs(60))

    @app.get("/jobs/<job_id>")
    def job_page(job_id: str):
        job = jobs.load(job_id)
        if not job:
            abort(404)
        return render_template("job.html", job=job)

    @app.get("/files/<path:rel>")
    def files(rel: str):
        path = (REPO_ROOT / rel).resolve()
        ok, why = data.servable(path)
        if not ok or not path.is_file():
            abort(404 if not path.exists() else 403, description=why)
        if path.suffix == ".md":
            return render_template("doc.html", title=path.name, body=render_file(path), rel=rel)
        mimetype = "text/plain; charset=utf-8" if path.suffix in (".txt", ".log", ".yaml", ".json") else None
        return send_file(path, mimetype=mimetype)

    # ---------------------------------------------------------------- session actions

    @app.post("/s/<name>/<action>")
    def session_action(name: str, action: str):
        s = data.find_session(name)
        if not s:
            abort(404)
        back = url_for("session", name=name)
        inc = s["incident"]
        if action == "start":
            return job_or_flash(["start", inc], f"Start {inc}", inc, back)
        if action == "grade":
            return job_or_flash(["grade", inc], f"Grade {inc}", inc, back)
        if action == "clean":
            return job_or_flash(["clean", "--id", inc, "--remove", "--yes"], f"Clean {inc}", inc, back)
        if action == "hint":
            code, out = jobs.run_now(["hint", inc, request.form.get("fault", "1")])
            flash(out.strip(), "ok" if code == 0 else "error")
            return redirect(back)
        if action == "notes":
            (s["path"] / "notes.md").write_text(request.form.get("notes", "").replace("\r\n", "\n"), encoding="utf-8")
            flash("Notes saved." + (" Run the debrief again to include them." if s["state"] == "debriefed" else ""),
                  "ok")
            return redirect(back + "#notes")
        if action == "debrief":
            code, out = jobs.run_now(["debrief", inc])
            flash(out.strip(), "ok" if code == 0 else "error")
            return redirect(url_for("session", name=name, view="debrief") if code == 0 else back)
        abort(404)

    # ---------------------------------------------------------------- lab and library actions

    @app.post("/labs/<lab>/healthy")
    def healthy(lab: str):
        return job_or_flash(["start", lab, "--healthy", "--clean-after"], f"Healthy run, lab {lab[:2]}", lab,
                            url_for("index"))

    @app.post("/doctor")
    def doctor():
        return job_or_flash(["doctor"], "Doctor", None, url_for("index"))

    @app.post("/selftest")
    def selftest_run():
        lab = request.form.get("lab", "")
        faults = request.form.getlist("fault")
        args = ["selftest", *faults, "--lab", lab]
        if not faults:
            args.append("--all")
        if request.form.get("keep"):
            args.append("--keep")
        title = f"Selftest {', '.join(faults) or 'all unproven'} on lab {lab[:2]}"
        return job_or_flash(args, title, f"selftest-{lab[:2]}", url_for("selftest"))

    @app.post("/batch")
    def batch_run():
        lab = request.form.get("lab", "")
        hours = request.form.get("hours", "9").strip() or "9"
        args = ["batch", "--lab", lab, "--hours", hours]
        if request.form.get("mark"):
            args.append("--mark")
        return job_or_flash(args, f"Overnight batch, lab {lab[:2]}", f"batch-{lab[:2]}", url_for("selftest"))

    @app.post("/selftest/mark")
    def mark():
        fault, _, lab = request.form.get("mark", "").partition("|")
        try:
            flash(data.mark_proven(fault, lab), "ok")
        except (ValueError, LabModelError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("selftest"))

    # ---------------------------------------------------------------- job API (the live console)

    @app.get("/api/jobs/<job_id>")
    def job_api(job_id: str):
        return jsonify(jobs.read(job_id, int(request.args.get("offset", 0))))

    @app.post("/api/jobs/<job_id>/stop")
    def job_stop(job_id: str):
        return jsonify({"stopped": jobs.stop(job_id)})

    @app.template_filter("since")
    def since(iso: str | None) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return iso

    return app


def serve(host: str = "127.0.0.1", port: int = 5050) -> None:
    from werkzeug.serving import make_server

    app = create_app()
    server = make_server(host, port, app, threaded=True)
    print(f"Breaker console on http://localhost:{port}  (Ctrl+C stops the console; running jobs keep going)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("console stopped")
