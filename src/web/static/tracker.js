"use strict";

(() => {
  const select = document.getElementById("tracker-batch");
  const status = document.getElementById("tracker-status");
  const content = document.getElementById("tracker-content");
  let generation = 0;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function evidence(ids, statements) {
    if (!Array.isArray(ids) || !ids.length || ids.some((id) => !statements.has(id))) {
      return null;
    }
    const list = node("div", null, "evidence-list");
    ids.forEach((id) => {
      const statement = statements.get(id);
      const quote = node("blockquote", statement.quote);
      const attribution = statement.speaker || "Unconfirmed speaker";
      quote.append(node("cite", `${attribution} · ${statement.date || "Undated"}`));
      if (statement.video_link) {
        const link = node("a", "Watch recording ↗");
        link.href = statement.video_link;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        quote.append(link);
      }
      list.append(quote);
    });
    return list;
  }

  function render(batch) {
    content.replaceChildren();
    const statements = new Map(batch.statements.map((item) => [item.statement_id, item]));
    if (batch.coverage) content.append(node("p", `Coverage: ${batch.coverage}`, "coverage"));
    const columns = node("div", null, "tracker-columns");
    const commitments = node("div", null, "tracker-column");
    commitments.append(node("p", "01 / COMMITMENTS", "eyebrow"), node("h4", "What was promised"));
    const promises = batch.statements.filter((item) => item.type === "commitment");
    if (!promises.length) commitments.append(node("p", "No verified commitments in this batch.", "muted"));
    promises.forEach((item) => {
      const card = node("article", null, "record-card");
      card.append(node("h5", item.topic || "Commitment"));
      if (item.commitment?.what) card.append(node("p", item.commitment.what));
      if (item.commitment?.deadline) card.append(node("p", `Deadline: ${item.commitment.deadline}`, "muted"));
      const quote = evidence([item.statement_id], statements);
      if (quote) card.append(quote);
      const follow = batch.follow_up;
      if (follow && follow.commitment === item.statement_id) {
        const citations = evidence(follow.evidence, statements);
        if (citations) {
          card.append(node("p", `Reviewed follow-up: ${follow.status || "unknown"}`, "follow-up"));
          if (follow.summary) card.append(node("p", follow.summary));
          card.append(citations);
        }
      }
      commitments.append(card);
    });
    const digest = node("div", null, "tracker-column");
    digest.append(node("p", "02 / MEETING DIGEST", "eyebrow"), node("h4", batch.digest?.title || "A meeting, in brief"));
    if (batch.digest?.scope) digest.append(node("p", batch.digest.scope, "muted"));
    let displayed = 0;
    for (const bullet of batch.digest?.bullets || []) {
      const citations = evidence(bullet.evidence, statements);
      if (!citations) continue;
      displayed += 1;
      const card = node("article", null, "record-card");
      card.append(node("span", bullet.category || "REVIEWED", "pill"), node("p", bullet.text));
      const details = node("details");
      details.append(node("summary", "View transcript evidence"), citations);
      card.append(details);
      digest.append(card);
    }
    if (!displayed) digest.append(node("p", "No digest with verified citations in this batch.", "muted"));
    columns.append(commitments, digest);
    content.append(columns);
    if (batch.actions.length) {
      const section = node("section", null, "tracker-actions");
      section.append(node("p", "03 / OFFICIAL ACTIONS", "eyebrow"), node("h4", "On the official record"));
      for (const action of batch.actions) {
        const description = action.type === "recorded_vote"
          ? `${action.person}: ${action.vote} · ${action.matter_id}`
          : `${action.person}: filing sponsor · ${action.matter_id}`;
        section.append(node("p", `${description} · ${action.date || "Undated"}`));
      }
      content.append(section);
    }
  }

  async function loadBatch() {
    const current = ++generation;
    status.textContent = "Loading reviewed records…";
    content.replaceChildren();
    try {
      const response = await fetch(`/api/tracker/batches/${encodeURIComponent(select.value)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load this tracker batch. Please try again.");
      const batch = await response.json();
      if (current !== generation) return;
      render(batch);
      status.textContent = "";
    } catch (error) {
      if (current === generation) status.textContent = error.message;
    }
  }

  async function init() {
    try {
      const response = await fetch("/api/tracker/batches", { cache: "no-store" });
      if (response.status === 503) throw new Error("The tracker is not available yet. A reviewed batch must be published to the analytics database.");
      if (!response.ok) throw new Error("Could not load the tracker. Please try again.");
      const batches = await response.json();
      select.replaceChildren();
      if (!batches.length) {
        select.append(node("option", "No published batches"));
        status.textContent = "No reviewed tracker batches have been published yet.";
        return;
      }
      batches.forEach((batch) => {
        const option = node("option", batch.title || batch.batch_id);
        option.value = batch.batch_id;
        select.append(option);
      });
      select.disabled = false;
      await loadBatch();
    } catch (error) {
      select.replaceChildren(node("option", "Unavailable"));
      status.textContent = error.message;
    }
  }
  select.addEventListener("change", loadBatch);
  init();
})();
