"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ui = require(path.join(__dirname, "..", "data", "training", "agency", "review", "review_studio_state.js"));
const html = fs.readFileSync(path.join(__dirname, "..", "data", "training", "agency", "review", "review_studio.html"), "utf8");
const records = [
  { id: "one", review: { status: "NEEDS_REVIEW" } },
  { id: "two", review: { status: "GOOD" } },
  { id: "three", review: { status: "NEEDS_REVIEW" } },
];

test("GOOD then Save & Next keeps the unchanged response and selects the next record", () => {
  assert.equal(ui.editedResponseStatus("same", "same", "GOOD"), "GOOD");
  assert.equal(ui.nextIndex(records, "one", 1), 1);
  assert.match(html, /id="saveNext"/);
  assert.match(html, /async function save\(next=false\)/);
});

test("editing a response automatically selects EDIT before Save & Next", () => {
  assert.equal(ui.editedResponseStatus("old", "new", "GOOD"), "EDIT");
  assert.match(html, /function responseChanged\(\)/);
  assert.match(html, /editResponse\(\).*\$\("message"\)\.focus\(\)/);
  assert.match(html, /Decision and reason are unchanged/);
});

test("DROP requires a confirmation before the state is selected", () => {
  assert.match(html, /function selectDrop\(\)\{if\(window\.confirm\(/);
  assert.match(html, /setStatus\("DROP"\)/);
});

test("Previous, Next, and Next unreviewed use stable review order", () => {
  assert.equal(ui.nextIndex(records, "two", -1), 0);
  assert.equal(ui.nextIndex(records, "two", 1), 2);
  assert.equal(ui.nextUnreviewedIndex(records, "one"), 2);
  assert.match(html, /id="previous"/);
  assert.match(html, /id="nextUnreviewed"/);
});

test("keyboard shortcuts are ignored while typing except intentional Ctrl+S save", () => {
  assert.equal(ui.shortcutAction({ key: "1" }, true), null);
  assert.equal(ui.shortcutAction({ key: "ArrowRight", altKey: true }, true), null);
  assert.equal(ui.shortcutAction({ key: "s", ctrlKey: true }, true), "save");
  assert.equal(ui.shortcutAction({ key: "2" }, false), "edit");
  assert.equal(ui.shortcutAction({ key: "ArrowLeft", altKey: true }, false), "previous");
});

test("unsaved review status, note, and record changes are all detected", () => {
  assert.equal(ui.isDirty({ message: "a" }, { message: "a" }, "GOOD", "GOOD", "", ""), false);
  assert.equal(ui.isDirty({ message: "a" }, { message: "b" }, "GOOD", "EDIT", "", ""), true);
  assert.match(html, /beforeunload/);
  assert.match(html, /unsaved changes/i);
});

test("existing review JSON import and atomic backend save route remain available", () => {
  assert.match(html, /id="import"/);
  assert.match(html, /api\("\/api\/import"/);
  assert.match(html, /api\("\/api\/save"/);
});

test("Advanced Editor retains every technical field", () => {
  for (const id of ["decisionEdit", "reason", "categoryEdit", "groupEdit", "familyEdit", "context", "memories", "actionsEdit", "candidates", "targetUid", "fullJson"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /Advanced Editor/);
});

test("proactive records use the candidate-focused layout", () => {
  assert.equal(ui.modeFor({ mode: "AGENCY_PROACTIVE" }), "proactive");
  assert.equal(ui.modeFor({ mode: "AGENCY_REACTIVE" }), "reactive");
  assert.match(html, /id="proactiveCandidates"/);
  assert.match(html, /function renderProactive\(record\)/);
});
