"use strict";

const element = (id) => document.getElementById(id);
const number = (value) => value === null ? "No samples" : value.toFixed(3);
let latest = null;

function clearMeasurements() {
  latest = null;
  element("classes").replaceChildren();
  element("tools").textContent = "Measurements unavailable";
  element("completion").textContent = "Completion percentiles: unavailable";
  element("window").textContent = "Measurement duration: unavailable";
  element("checked").textContent = "Last successful validation: none";
}

function renderSummary(payload) {
  const summary = payload.summary;
  element("classes").replaceChildren();
  for (const name of payload.class_order) {
    const report = summary.classes[name];
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = name;
    row.append(label);
    const values = [
      ...["p50", "p95", "p99"].map((key) => number(report.latency_ms?.[key] ?? null)),
      number(report.throughput_mbps), report.sent, report.received, report.lost
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    element("classes").append(row);
  }
  const calls = summary.tool_calls;
  element("tools").textContent = `Started: ${calls.started} · Completed: ${calls.completed}` +
    ` · Incomplete: ${calls.incomplete} · Completion fraction: ${number(calls.completion_fraction)}`;
  element("completion").textContent = "Completed calls (ms): " +
    ["p50", "p95", "p99"].map((key) => `${key}: ${number(calls.completion_ms?.[key] ?? null)}`).join(" · ");
  element("window").textContent = `Measurement duration: ${number(summary.duration_s)} seconds`;
  element("checked").textContent = `Last successful validation: ${payload.checked_at}`;
}

function renderSourceTime() {
  const timestamp = latest?.source_updated_at;
  if (!timestamp) {
    element("updated").textContent = "Source modification time: unavailable";
    return;
  }
  const age = Math.max(0, (Date.now() - Date.parse(timestamp)) / 1000);
  element("updated").textContent = `Source last modified: ${timestamp} · ${Math.floor(age)} seconds ago`;
}

async function poll() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch("/api/metrics", {cache: "no-store", signal: controller.signal});
    const payload = await response.json();
    if (!response.ok || payload.status !== "ready") {
      clearMeasurements();
      latest = payload;
      element("status").textContent = payload.message;
      element("status").dataset.state = "unavailable";
    } else {
      renderSummary(payload);
      latest = payload;
      element("status").textContent = payload.message;
      element("status").dataset.state = "ready";
    }
  } catch (error) {
    clearMeasurements();
    element("status").textContent = "Measurement API unavailable. Waiting for the next poll.";
    element("status").dataset.state = "unavailable";
  } finally {
    clearTimeout(timeout);
    renderSourceTime();
    setTimeout(poll, 2000);
  }
}

setInterval(renderSourceTime, 1000);
poll();
