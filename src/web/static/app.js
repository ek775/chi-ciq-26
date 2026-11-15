"use strict";

const element = (id) => document.getElementById(id);
const status = element("status");
let meetingOffset = 0;
let segmentOffset = 0;
let selected = null;
let meetingQuery = "";
let segmentQuery = "";
let meetingGeneration = 0;
let segmentGeneration = 0;
const pageSize = 50;

async function request(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}). Please retry.`);
  }
  return response.json();
}

function timestamp(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 3600)}:${String(Math.floor(whole / 60) % 60).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

async function loadMeetings(reset = false) {
  const generation = ++meetingGeneration;
  if (reset) {
    meetingOffset = 0;
    element("meetings").replaceChildren();
  }
  element("more-meetings").disabled = true;
  status.textContent = "Loading meetings...";
  try {
    const params = new URLSearchParams({ q: meetingQuery, offset: meetingOffset, limit: pageSize });
    const rows = await request(`/api/meetings?${params}`);
    if (generation !== meetingGeneration) return;
    for (const meeting of rows) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.textContent = `${meeting.meeting_date ?? "Undated"} - ${meeting.meeting_type ?? meeting.filename}`;
      button.addEventListener("click", () => selectMeeting(meeting));
      item.append(button);
      element("meetings").append(item);
    }
    meetingOffset += rows.length;
    element("more-meetings").hidden = rows.length < pageSize;
    status.textContent = meetingOffset ? `${meetingOffset} meetings shown.` : "No matching meetings.";
  } catch (error) {
    if (generation === meetingGeneration) status.textContent = error.message;
  } finally {
    if (generation === meetingGeneration) element("more-meetings").disabled = false;
  }
}

function selectMeeting(meeting) {
  selected = meeting;
  segmentQuery = "";
  element("segment-query").value = "";
  element("segment-search").hidden = false;
  element("title").textContent = `${meeting.meeting_date ?? "Undated"} - ${meeting.meeting_type ?? meeting.filename}`;
  loadSegments(true);
}

async function loadSegments(reset = false) {
  if (!selected) return;
  const generation = ++segmentGeneration;
  if (reset) {
    segmentOffset = 0;
    element("segments").replaceChildren();
  }
  element("more-segments").disabled = true;
  status.textContent = "Loading transcript...";
  try {
    const params = new URLSearchParams({ q: segmentQuery, offset: segmentOffset, limit: pageSize });
    const rows = await request(`/api/meetings/${selected.id}/segments?${params}`);
    if (generation !== segmentGeneration) return;
    for (const segment of rows) {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = `${timestamp(segment.start_seconds)} - ${timestamp(segment.end_seconds)} | ${segment.speaker}`;
      const words = document.createElement("p");
      words.textContent = segment.text;
      item.append(label, words);
      element("segments").append(item);
    }
    segmentOffset += rows.length;
    element("more-segments").hidden = rows.length < pageSize;
    status.textContent = segmentOffset ? `${segmentOffset} transcript segments shown.` : "No transcript segments match.";
  } catch (error) {
    if (generation === segmentGeneration) status.textContent = error.message;
  } finally {
    if (generation === segmentGeneration) element("more-segments").disabled = false;
  }
}

element("search").addEventListener("submit", (event) => {
  event.preventDefault();
  meetingQuery = element("query").value.trim();
  loadMeetings(true);
});
element("segment-search").addEventListener("submit", (event) => {
  event.preventDefault();
  segmentQuery = element("segment-query").value.trim();
  loadSegments(true);
});
element("more-meetings").addEventListener("click", () => loadMeetings());
element("more-segments").addEventListener("click", () => loadSegments());
loadMeetings(true);
