/* WuXi -> CDD import — frontend. Talks to the local FastAPI backend; only
   column names + counts ever come back (never cell values). */
"use strict";

const $ = (id) => document.getElementById(id);
const dropEl = $("drop"), fileEl = $("file"), panelEl = $("panel");
const rowsEl = $("rows"), submitEl = $("submit"), clearEl = $("clear");
const gateEl = $("gate"), footEl = $("foot");
const summaryEl = $("summary"), summaryRowsEl = $("summaryRows");

let PROTOCOLS = [];       // [{pid,name,alias}] for the dropdowns
let UNITS = [];           // current submission units (server views)
const SLURP = {};         // file_id -> slurp_id (after submit)
let polling = null;
let summaryEmailed = false;   // guard: send the batch summary email at most once
let verified = false;         // guard: run the CDD verification at most once

function foot(msg){ footEl.textContent = msg; }

async function loadProtocols(){
  const r = await fetch("/api/protocols");
  PROTOCOLS = (await r.json()).protocols || [];
}

// ---- rendering ----------------------------------------------------------

function protocolSelect(u){
  const sel = document.createElement("select");
  const blank = new Option("— select protocol —", "");
  blank.disabled = true;
  sel.add(blank);
  for (const p of PROTOCOLS) sel.add(new Option(`${p.name} (${p.pid})`, String(p.pid)));
  sel.value = u.protocol_pid ? String(u.protocol_pid) : "";
  if (!u.protocol_pid) blank.selected = true;
  sel.disabled = (u.status === "error") || !!SLURP[u.file_id];
  sel.onchange = () => recheck(u.file_id, sel.value);
  return sel;
}

function statusCell(u){
  const td = document.createElement("td");
  const tag = document.createElement("span");
  const sid = SLURP[u.file_id];
  if (sid !== undefined){
    // post-submit: progress state lives here (updated by polling)
    tag.className = "tag wait"; tag.textContent = "queued";
    tag.dataset.progress = "1";
  } else if (u.status === "ready"){
    tag.className = "tag ok"; tag.textContent = "ready";
  } else if (u.status === "error"){
    tag.className = "tag err"; tag.textContent = "error";
  } else {
    tag.className = "tag wait"; tag.textContent = "needs protocol";
  }
  td.appendChild(tag);

  const notes = [];
  if (u.status === "error") notes.push(u.protocol_name || "no Upload tab");
  if (u.unmapped && u.unmapped.length) notes.push("unmapped: " + u.unmapped.join(", "));
  if (u.missing && u.missing.length) notes.push("missing: " + u.missing.join(", "));
  if (u.dropped_blank_id) notes.push(u.dropped_blank_id + " blank-id rows dropped");
  if (u.renamed_identifier) notes.push("identifier renamed for MDR1");
  if (notes.length){
    const s = document.createElement("span");
    s.className = "sub"; s.textContent = notes.join(" · ");
    td.appendChild(s);
  }
  return td;
}

function render(){
  rowsEl.innerHTML = "";
  for (const u of UNITS){
    const tr = document.createElement("tr");
    tr.dataset.fid = u.file_id;

    const f = document.createElement("td");
    f.className = "fname";
    f.innerHTML = `${u.parent}` + (u.species ? `<small>species: ${u.species}</small>` : "");
    tr.appendChild(f);

    const p = document.createElement("td");
    p.appendChild(protocolSelect(u));
    tr.appendChild(p);

    const rc = document.createElement("td");
    rc.className = "rows"; rc.textContent = u.rows;
    tr.appendChild(rc);

    tr.appendChild(statusCell(u));
    rowsEl.appendChild(tr);
  }
  updateGate();
}

function updateGate(){
  const submitted = Object.keys(SLURP).length > 0;
  const allReady = UNITS.length > 0 && UNITS.every(u => u.status === "ready");
  if (submitted){
    submitEl.classList.remove("ready"); submitEl.disabled = true;
    gateEl.textContent = ""; return;
  }
  submitEl.classList.toggle("ready", allReady);
  submitEl.disabled = !allReady;
  const notReady = UNITS.filter(u => u.status !== "ready").length;
  gateEl.textContent = UNITS.length === 0 ? "" :
    allReady ? `${UNITS.length} row(s) confirmed — ready to submit`
             : `${notReady} of ${UNITS.length} row(s) need a protocol`;
}

// per-compound rollup table (compound ids are shown here — local browser only)
async function renderSummary(){
  const r = await fetch("/api/summary");
  const {compounds} = await r.json();
  summaryRowsEl.innerHTML = "";
  for (const c of compounds){
    const tr = document.createElement("tr");
    const a = document.createElement("td"); a.className = "rows"; a.textContent = c.batch_id;
    const d = document.createElement("td"); d.className = "rows"; d.textContent = c.run_date || "";
    const b = document.createElement("td"); b.textContent = c.assays.join(", ");
    tr.appendChild(a); tr.appendChild(d); tr.appendChild(b); summaryRowsEl.appendChild(tr);
  }
  summaryEl.classList.toggle("show", compounds.length > 0);
}

// ---- backend calls ------------------------------------------------------

async function upload(fileList){
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  foot("reading upload tabs…");
  const r = await fetch("/api/upload", {method:"POST", body:fd});
  if (!r.ok){ foot("upload failed: " + r.status); return; }
  const data = await r.json();
  UNITS = UNITS.concat(data.units || []);
  panelEl.classList.add("show");
  render();
  renderSummary();
  foot(`${UNITS.length} submission unit(s)`);
}

async function recheck(fid, pid){
  const r = await fetch("/api/recheck", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({file_id: fid, pid: pid ? Number(pid) : null})
  });
  const u = await r.json();
  const i = UNITS.findIndex(x => x.file_id === fid);
  if (i >= 0) UNITS[i] = u;
  render();
  renderSummary();
}

async function submit(){
  if (submitEl.disabled) return;
  summaryEmailed = false; verified = false;
  const units = UNITS.map(u => ({file_id: u.file_id}));
  foot("submitting to CDD…");
  submitEl.disabled = true; submitEl.classList.remove("ready");
  const r = await fetch("/api/submit", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({units})
  });
  if (!r.ok){ foot("submit failed: " + r.status + " (token?)"); return; }
  const {results} = await r.json();
  for (const res of results){
    const tr = rowsEl.querySelector(`tr[data-fid="${res.file_id}"]`);
    const tag = tr && tr.querySelector(".tag");
    if (res.slurp_id !== undefined){
      SLURP[res.file_id] = res.slurp_id;
      if (tag){ tag.className = "tag wait"; tag.textContent = "queued"; tag.dataset.progress = "1"; }
    } else if (tag){
      tag.className = "tag err"; tag.textContent = "rejected";
      showError(tr, res);
    }
  }
  render();
  startPolling();
}

function showError(tr, res){
  const td = tr.children[3];
  const s = document.createElement("span");
  s.className = "sub";
  s.innerHTML = (res.error || "error") +
    (res.web_url ? ` · <a href="${res.web_url}" target="_blank" rel="noopener">fix mapping in CDD ↗</a>` : "");
  td.appendChild(s);
}

// ---- progress polling ---------------------------------------------------

const TERMINAL = new Set(["committed","rejected","invalid","canceled"]);
const TAGCLASS = {committed:"ok", rejected:"err", invalid:"err", canceled:"err"};

function startPolling(){
  if (polling) return;
  const ids = Object.values(SLURP);
  if (!ids.length) return;
  const tick = async () => {
    const r = await fetch("/api/status?ids=" + ids.join(","));
    const {statuses} = await r.json();
    let done = 0;
    for (const st of statuses){
      const fid = Object.keys(SLURP).find(k => SLURP[k] === st.slurp_id);
      const tr = fid && rowsEl.querySelector(`tr[data-fid="${fid}"]`);
      const tag = tr && tr.querySelector(".tag[data-progress]");
      if (tag){
        tag.className = "tag " + (TAGCLASS[st.state] || "wait");
        tag.textContent = st.state === "committed" && st.records_committed != null
          ? `committed ${st.records_committed}/${st.total_records}` : st.state;
      }
      if (TERMINAL.has(st.state)) done++;
    }
    foot(`progress: ${done}/${statuses.length} finished`);
    if (done >= statuses.length){
      clearInterval(polling); polling = null; foot("done");
      verifyBatch(); emailSummary();
    }
  };
  tick();
  polling = setInterval(tick, 4000);
}

// verify committed data against CDD; flip rows + the button to azure SUCCESS
async function verifyBatch(){
  if (verified) return;
  verified = true;
  foot("verifying against CDD…");
  try {
    const r = await fetch("/api/verify", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
    const {results, success} = await r.json();
    for (const res of results){
      const tr = rowsEl.querySelector(`tr[data-fid="${res.file_id}"]`);
      const tag = tr && tr.querySelector(".tag");
      if (!tag) continue;
      tag.className = "tag " + (res.ok ? "azure" : "err");
      tag.textContent = res.ok ? "SUCCESS"
        : `verify failed (${res.mismatch} mismatch · ${res.missing} missing)`;
    }
    if (success){ submitEl.textContent = "SUCCESS"; submitEl.classList.add("success"); }
    foot(success ? "verified · SUCCESS" : "verified · mismatches (see rows)");
  } catch (e) { foot("verify failed to run"); }
}

// send the per-compound summary to the team once the batch finishes (at most once)
async function emailSummary(){
  if (summaryEmailed) return;
  summaryEmailed = true;
  try {
    const r = await fetch("/api/email-summary", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
    const d = await r.json();
    foot(d.sent ? `done · summary emailed (${d.compounds} compounds)`
                : "done · summary email not sent (see server log)");
  } catch (e) { foot("done · summary email failed"); }
}

// ---- wiring -------------------------------------------------------------

dropEl.addEventListener("click", () => fileEl.click());
dropEl.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " "){ e.preventDefault(); fileEl.click(); }});
fileEl.addEventListener("change", () => { if (fileEl.files.length) upload(fileEl.files); fileEl.value = ""; });

["dragenter","dragover","dragleave","drop"].forEach(ev =>
  document.addEventListener(ev, (e) => e.preventDefault(), false));
document.addEventListener("dragover", () => dropEl.classList.add("drag"), false);
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) dropEl.classList.remove("drag"); }, false);
document.addEventListener("drop", (e) => {
  dropEl.classList.remove("drag");
  const files = e.dataTransfer && e.dataTransfer.files;
  if (files && files.length) upload(files);
}, false);

submitEl.addEventListener("click", submit);
clearEl.addEventListener("click", () => {
  UNITS = []; for (const k in SLURP) delete SLURP[k];
  if (polling){ clearInterval(polling); polling = null; }
  summaryEmailed = false; verified = false;
  submitEl.textContent = "Submit to CDD"; submitEl.classList.remove("success");
  summaryEl.classList.remove("show"); summaryRowsEl.innerHTML = "";
  panelEl.classList.remove("show"); rowsEl.innerHTML = ""; foot("idle");
});

loadProtocols().then(() => foot("ready"));
