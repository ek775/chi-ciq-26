"use strict";

const element = (id) => document.getElementById(id);
const status = element("status");
const pageSize = 25;
const filters = { representative_id: "", topic_key: "" };
let statementOffset = 0;
let statementGeneration = 0;
let detailGeneration = 0;

async function request(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (response.status === 503) {
    throw new Error("Analytics are unavailable. Run the analytics pipeline and retry.");
  }
  if (!response.ok) throw new Error(`Request failed (${response.status}). Please retry.`);
  return response.json();
}

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function number(value, digits = 2) {
  return value === null || value === undefined ? "-" : Number(value).toFixed(digits);
}

function signed(value) {
  if (value === null || value === undefined) return "-";
  return `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}`;
}

function row(cells, onSelect) {
  const tr = document.createElement("tr");
  cells.forEach((cell, index) => {
    const td = document.createElement("td");
    if (index === 0 && onSelect) {
      const button = node("button", cell, "link");
      button.type = "button";
      button.addEventListener("click", onSelect);
      td.append(button);
    } else if (cell instanceof Node) {
      td.append(cell);
    } else {
      td.textContent = cell ?? "-";
    }
    tr.append(td);
  });
  return tr;
}

function stanceBar(support, oppose) {
  const total = support + oppose;
  const bar = node("span", null, "stance-bar");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", `${support} support, ${oppose} oppose`);
  const yes = node("span", null, "support");
  const no = node("span", null, "oppose");
  yes.style.width = total ? `${(100 * support) / total}%` : "0";
  no.style.width = total ? `${(100 * oppose) / total}%` : "0";
  bar.append(yes, no);
  const wrapper = node("span");
  wrapper.append(bar, node("span", ` ${support} / ${oppose}`));
  return wrapper;
}

async function loadSummary() {
  const summary = await request("/api/analytics/summary");
  const cards = [
    ["Representatives with statements", `${summary.attributed_representatives} of ${summary.representatives}`],
    ["Policy positions", summary.positions],
    ["Meetings", summary.meetings],
    ["Transcript documents", summary.documents],
  ];
  const refresh = [
    ["Last refresh", summary.refreshed_at ? new Date(summary.refreshed_at).toLocaleString() : "never"],
    ["Last run", summary.last_run ? summary.last_run.status : "none"],
  ];
  element("summary").replaceChildren(
    ...cards.map(([label, value]) => {
      const wrapper = node("div");
      wrapper.append(node("dt", label), node("dd", value));
      return wrapper;
    }),
  );
  element("analysis-status").replaceChildren(
    ...refresh.flatMap(([label, value]) => [node("dt", label), node("dd", value)]),
  );
  if (!summary.refreshed_at && !summary.topics && !summary.representatives) {
    status.textContent = "No policy analytics are available in this database yet.";
  }
}

async function loadTopics() {
  const rows = await request(`/api/analytics/topics?sort=${element("topic-sort").value}`);
  element("topics").replaceChildren(
    ...rows.map((topic) =>
      row(
        [topic.label, topic.statements, stanceBar(topic.support, topic.oppose), signed(topic.stance_score)],
        () => selectTopic(topic.topic_key),
      ),
    ),
  );
}

async function loadRepresentatives() {
  const sort = element("representative-sort").value;
  const rows = await request(`/api/analytics/representatives?sort=${sort}&limit=200`);
  element("representatives").replaceChildren(
    ...rows.map((person) =>
      row(
        [person.display_name, person.ward, person.statements, signed(person.mean_sentiment), person.dominant_emotion],
        () => selectRepresentative(person.representative_id),
      ),
    ),
  );
}

function showDetail(title, description, columns, rows, trend) {
  element("detail-heading").textContent = title;
  element("detail-description").textContent = description;
  element("detail-columns").replaceChildren(...columns.map((name) => {
    const th = node("th", name);
    th.scope = "col";
    return th;
  }));
  element("detail-rows").replaceChildren(...rows);
  element("detail-table").hidden = rows.length === 0;
  element("detail-trend").replaceChildren(...trend);
}

function trendBar(label, value, caption) {
  const item = node("div", null, "trend");
  const bar = node("span", null, value < 0 ? "oppose" : "support");
  bar.style.width = `${Math.min(Math.abs(value ?? 0), 1) * 50}%`;
  bar.style.marginLeft = value < 0 ? `${50 - Math.min(Math.abs(value), 1) * 50}%` : "50%";
  const track = node("span", null, "track");
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", `${label}: ${caption}`);
  track.append(bar);
  item.append(node("span", label), track, node("span", caption));
  return item;
}

async function selectTopic(key) {
  const generation = ++detailGeneration;
  status.textContent = "Loading topic...";
  try {
    const topic = await request(`/api/analytics/topics/${encodeURIComponent(key)}`);
    if (generation !== detailGeneration) return;
    showDetail(
      topic.label,
      `${topic.proposition} Score runs from -1 (opposes) to +1 (supports).`,
      ["Representative", "Statements", "Support / oppose", "Score", "Manual"],
      topic.positions.map((position) =>
        row(
          [position.display_name, position.statements, stanceBar(position.support, position.oppose), signed(position.stance_score), position.verified_speaker],
          () => selectRepresentative(position.representative_id),
        ),
      ),
      topic.trend.map((point) => trendBar(point.month.slice(0, 7), point.stance_score, `${signed(point.stance_score)} (${point.statements})`)),
    );
    filters.topic_key = key;
    filters.representative_id = "";
    status.textContent = "";
    await loadStatements(true);
  } catch (error) {
    if (generation === detailGeneration) status.textContent = error.message;
  }
}

async function selectRepresentative(id) {
  const generation = ++detailGeneration;
  status.textContent = "Loading representative...";
  try {
    const person = await request(`/api/analytics/representatives/${encodeURIComponent(id)}`);
    if (generation !== detailGeneration) return;
    const ward = person.ward ? `Ward ${person.ward}, ` : "";
    showDetail(
      person.display_name,
      `${ward}${person.role}. ${person.statements} attributed statements ` +
        `(${person.verified_statements} manually labeled) in ${person.sources} sources; ` +
        `mentioned ${person.mentions} times. Bars show monthly mean sentiment.`,
      ["Topic", "Statements", "Support / oppose", "Score", "Confidence"],
      person.positions.map((position) =>
        row(
          [position.label, position.statements, stanceBar(position.support, position.oppose), signed(position.stance_score), number(position.mean_confidence)],
          () => selectTopic(position.topic_key),
        ),
      ),
      person.monthly.map((point) => trendBar(point.month.slice(0, 7), point.mean_sentiment, `${signed(point.mean_sentiment)} (${point.statements})`)),
    );
    filters.representative_id = id;
    filters.topic_key = "";
    status.textContent = "";
    await loadStatements(true);
  } catch (error) {
    if (generation === detailGeneration) status.textContent = error.message;
  }
}

function statementItem(statement) {
  const item = document.createElement("li");
  const who = statement.representative_id && statement.attribution_method === "manual"
    ? `${statement.representative_id} (manually labeled)`
    : "Unconfirmed speaker";
  const header = node("p", null, "meta");
  header.append(
    node("strong", `${statement.stance.toUpperCase()} · ${statement.topic_key}`),
    node("span", ` · ${who} · ${statement.meeting_date ?? "undated"} · ${statement.body} (${statement.kind})`),
    node("span", ` · confidence ${number(statement.confidence)}`),
  );
  item.append(header);
  if (statement.summary) item.append(node("p", statement.summary));
  if (statement.evidence && statement.evidence_verified) {
    const quote = node("blockquote", statement.evidence);
    item.append(quote);
  }
  const details = document.createElement("details");
  details.append(node("summary", "Full passage"), node("p", statement.text, "passage"));
  item.append(details);
  return item;
}

async function loadStatements(reset = false) {
  const generation = ++statementGeneration;
  if (reset) {
    statementOffset = 0;
    element("statements").replaceChildren();
  }
  const form = new FormData(element("statement-filters"));
  const params = new URLSearchParams({ limit: pageSize, offset: statementOffset });
  for (const [key, value] of [...form.entries(), ...Object.entries(filters)]) {
    if (value) params.set(key, value);
  }
  // A representative-scoped quote must never be attached through voice matching.
  if (filters.representative_id && !params.has("method")) params.set("method", "manual");
  const scope = [filters.representative_id, filters.topic_key].filter(Boolean).join(" · ");
  element("statement-scope").textContent = scope
    ? `Showing: ${scope}${filters.representative_id ? " · manually attributed speakers only" : ""}`
    : "Showing all statements";
  element("more-statements").disabled = true;
  try {
    const rows = await request(`/api/analytics/statements?${params}`);
    if (generation !== statementGeneration) return;
    element("statements").append(...rows.map(statementItem));
    statementOffset += rows.length;
    element("more-statements").hidden = rows.length < pageSize;
    if (!statementOffset) element("statements").append(node("li", "No matching statements."));
  } catch (error) {
    if (generation === statementGeneration) status.textContent = error.message;
  } finally {
    if (generation === statementGeneration) element("more-statements").disabled = false;
  }
}

async function guarded(task) {
  try {
    await task();
  } catch (error) {
    status.textContent = error.message;
  }
}

element("topic-sort").addEventListener("change", () => guarded(loadTopics));
element("representative-sort").addEventListener("change", () => guarded(loadRepresentatives));
element("statement-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  loadStatements(true);
});
element("clear-filters").addEventListener("click", () => {
  filters.representative_id = "";
  filters.topic_key = "";
  loadStatements(true);
});
element("more-statements").addEventListener("click", () => loadStatements());

guarded(async () => {
  await Promise.all([loadSummary(), loadTopics(), loadRepresentatives()]);
  await loadStatements(true);
});
