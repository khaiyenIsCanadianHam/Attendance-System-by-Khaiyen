"use strict";
document.addEventListener("DOMContentLoaded", () => {
    const byId = id => document.getElementById(id);
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const status = message => { byId("status").textContent = message; };
    let scanner = null, processing = false;
    const durations = new Map();
    async function api(path, body) {
        const response = await fetch(path, {
            method: body === undefined ? "GET" : "POST",
            headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        if (response.status === 401) { window.location.assign("/"); throw new Error("Sign in again."); }
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Request failed");
        return data;
    }
    function cell(row, text) {
        const td = document.createElement("td");
        td.textContent = text;
        row.append(td);
        return td;
    }
    function render(entries) {
        const list = byId("attendanceList");
        list.replaceChildren(); durations.clear();
        byId("count").textContent = entries.filter(e => !e.time_out).length;
        if (!entries.length) {
            const row = document.createElement("tr");
            const td = cell(row, "No attendance recorded yet.");
            td.colSpan = 6; td.className = "empty"; list.append(row);
        }
        for (const entry of entries) {
            const row = document.createElement("tr"); row.dataset.record = "true";
            const name = cell(row, entry.full_name);
            const lrn = document.createElement("small"); lrn.textContent = entry.lrn; name.append(lrn);
            cell(row, entry.section);
            cell(row, new Date(entry.time_in).toLocaleString());
            cell(row, entry.time_out ? new Date(entry.time_out).toLocaleString() : "—");
            const duration = cell(row, entry.time_out ? entry.duration : "0:00:00");
            if (!entry.time_out) durations.set(duration, new Date(entry.time_in));
            const action = cell(row, "");
            if (!entry.time_out) {
                const button = document.createElement("button");
                button.className = "secondary"; button.textContent = "Time out";
                button.setAttribute("aria-label", `Time out ${entry.full_name}`);
                button.onclick = async () => {
                    button.disabled = true;
                    try { await api("/timeout", {lrn: entry.lrn}); await refresh(); status(`${entry.full_name} timed out.`); }
                    catch (error) { status(error.message); button.disabled = false; }
                };
                action.append(button);
            }
            list.append(row);
        }
    }
    async function refresh() { render(await api("/get_attendance")); }
    async function refreshSync() {
        const data = await api("/sync_status");
        let message = `Roster: ${data.students} ${data.students === 1 ? "student" : "students"} · ${data.pending} pending`;
        if (data.state === "disabled") message += " · Sheet not connected";
        else if (data.state === "error") message += " · Sync failed; check the Sheet";
        else if (data.state === "stale") message += " · Sync stopped; restart the worker";
        else if (data.last_success) message += ` · Updated ${new Date(data.last_success).toLocaleTimeString()}`;
        else message += " · Waiting for first sync";
        byId("syncStatus").textContent = message;
    }
    byId("recordBtn").onclick = async () => {
        if (scanner) return;
        byId("recordBtn").disabled = true; byId("stopBtn").disabled = false;
        byId("reader").hidden = false;
        const candidate = new QrCamera(byId("reader"), async text => {
            if (processing) return;
            processing = true;
            try {
                const result = await api("/scan", {data:text});
                status(`Checked in ${result.student.full_name}`);
                await refresh();
            } catch (error) { status(error.message); }
            finally { setTimeout(() => { processing = false; },1200); }
        });
        scanner = candidate;
        try {
            if (!await candidate.start() || scanner !== candidate) return;
            status("Scanner ready. Verify the student's identity.");
        } catch (error) {
            await candidate.stop();
            if (scanner === candidate) scanner = null;
            byId("reader").hidden = true; byId("recordBtn").disabled = false; byId("stopBtn").disabled = true;
            status("Camera unavailable. Allow camera access and use HTTPS or localhost.");
        }
    };
    byId("stopBtn").onclick = async () => {
        if (scanner) { const current = scanner; scanner = null; await current.stop(); }
        byId("reader").hidden = true; byId("recordBtn").disabled = false; byId("stopBtn").disabled = true;
        status("Scanner stopped.");
    };
    byId("timeoutAllBtn").onclick = async () => {
        if (!confirm("Time out all active students?")) return;
        try { await api("/timeout_all", {}); await refresh(); status("All students timed out."); }
        catch (error) { status(error.message); }
    };
    byId("downloadBtn").onclick = async () => {
        try {
            const response = await fetch("/export_csv");
            if (!response.ok) throw new Error("Could not download report. Check your login.");
            const url = URL.createObjectURL(await response.blob());
            const a = document.createElement("a"); a.href = url; a.download = "hanami-attendance.csv";
            a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
            status("Report downloaded.");
        } catch (error) { status(error.message); }
    };
    setInterval(() => {
        for (const [element, start] of durations) {
            const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
            element.textContent = `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
        }
    }, 1000);
    refresh().catch(error => status(error.message));
    refreshSync().catch(error => { byId("syncStatus").textContent = error.message; });
    setInterval(() => refreshSync().catch(error => { byId("syncStatus").textContent = error.message; }),15000);
});
