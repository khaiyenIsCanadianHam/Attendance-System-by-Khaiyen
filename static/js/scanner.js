"use strict";
document.addEventListener("DOMContentLoaded", () => {
    const byId = id => document.getElementById(id);
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const status = message => { byId("status").textContent = message; };
    let scanner = null, processing = false, entries = [];
    const durations = new Map();
    async function api(path, body) {
        const response = await fetch(path, {
            method: body === undefined ? "GET" : "POST",
            headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        if (response.status === 401) { window.location.assign("/"); throw new Error("Please log in again."); }
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Request failed");
        return data;
    }
    function render() {
        const list = byId("attendanceList");
        list.replaceChildren(); durations.clear();
        byId("count").textContent = entries.filter(e => !e.time_out).length;
        for (const entry of entries) {
            const li = document.createElement("li");
            const title = document.createElement("strong");
            title.textContent = `${entry.full_name} (${entry.section})`;
            const time = document.createElement("p");
            time.textContent = `Time In: ${new Date(entry.time_in).toLocaleString()}`;
            const duration = document.createElement("p");
            if (entry.time_out) duration.textContent = `Time Out: ${new Date(entry.time_out).toLocaleString()}`;
            else durations.set(duration, new Date(entry.time_in));
            li.append(title, time, duration);
            if (!entry.time_out) {
                const button = document.createElement("button");
                button.textContent = "Time Out";
                button.onclick = async () => {
                    button.disabled = true;
                    try { await api("/timeout", {lrn: entry.lrn}); await refresh(); }
                    catch (error) { status(error.message); button.disabled = false; }
                };
                li.append(button);
            }
            list.append(li);
        }
    }
    async function refresh() { entries = await api("/get_attendance"); render(); }
    byId("recordBtn").onclick = async () => {
        if (scanner) return;
        byId("recordBtn").disabled = true;
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
            byId("reader").hidden = true;
            status("Camera unavailable. Use HTTPS, grant camera permission, and try again.");
        } finally { byId("recordBtn").disabled = false; }
    };
    byId("timeoutAllBtn").onclick = async () => {
        if (!confirm("Time out all active students?")) return;
        try { await api("/timeout_all", {}); await refresh(); status("All students timed out."); }
        catch (error) { status(error.message); }
    };
    byId("endBtn").onclick = async () => {
        try {
            if (scanner) { await scanner.stop(); scanner = null; byId("reader").hidden = true; }
            const response = await fetch("/export_csv");
            if (!response.ok) throw new Error("Could not download report. Check your login.");
            const url = URL.createObjectURL(await response.blob());
            const a = document.createElement("a"); a.href = url; a.download = "attendance.csv";
            a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
            status("Scanner stopped. Active records remain until you time students out.");
        } catch (error) { status(error.message); }
    };
    setInterval(() => {
        for (const [element, start] of durations) {
            const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
            element.textContent = `Duration: ${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
        }
    }, 1000);
    refresh().catch(error => status(error.message));
});
