const select = document.getElementById("notes");
const panel = document.getElementById("selected-note");
const status = document.getElementById("status");
const reviewPanel = document.getElementById("review-card");
const deckSelect = document.getElementById("deck");
const reviewMode = document.getElementById("review-mode");
let reviewQueue = [];
let reviewPosition = 0;
let reviewRequest = 0;
const sourceNames = { obsidian: "Obsidian", goodnotes: "Goodnotes", manual: "Pasted" };
let openNoteRequest = 0;

async function api(path, data) {
  const options = data === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

function node(tag, content) {
  const element = document.createElement(tag);
  if (content !== undefined) element.textContent = content;
  return element;
}

function action(label, run) {
  const button = node("button", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "";
    try {
      await run();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

async function refreshNotes() {
  const notes = await api("/api/notes");
  const previous = select.value;
  const options = notes.map(({ id, source, name }) => new Option(sourceNames[source] + " / " + name, id));
  select.replaceChildren(new Option("Choose a note", ""), ...options);
  if (options.some(option => option.value === previous)) select.value = previous;
  return notes;
}

function showDraft(draft) {
  const container = node("article");
  const front = node("textarea");
  const back = node("textarea");
  front.value = draft.front;
  back.value = draft.back;
  front.rows = 2;
  back.rows = 3;
  front.maxLength = 500;
  back.maxLength = 2000;
  front.setAttribute("aria-label", "Card question");
  back.setAttribute("aria-label", "Card answer");
  const submit = async (decision) => {
    for (const button of container.querySelectorAll("button")) button.disabled = true;
    try {
      await api("/api/cards/" + draft.id + "/draft", {
        action: decision, front: front.value, back: back.value
      });
      container.remove();
      if (decision === "approve") await loadReview();
    } finally {
      for (const button of container.querySelectorAll("button")) button.disabled = false;
    }
  };
  container.append(front, back, node("blockquote", draft.evidence),
    action("Approve", () => submit("approve")), action("Discard", () => submit("discard")));
  return container;
}

async function openNote(id, offset = 0) {
  if (!id) return;
  const request = ++openNoteRequest;
  const note = await api("/api/notes/" + id);
  if (request !== openNoteRequest) return;
  if (!note.body) {
    panel.replaceChildren(node("h3", note.name), node("p", "This note is empty."));
    return;
  }
  const codePoints = Array.from(note.body);
  const sections = node("select");
  sections.setAttribute("aria-label", "Note section");
  for (let start = 0; start < codePoints.length; start += note.sectionSize) {
    sections.add(new Option("Section " + (sections.length + 1), start));
  }
  sections.value = offset < codePoints.length ? offset : 0;
  const excerpt = node("pre");
  const showSection = () => {
    const start = Number(sections.value);
    excerpt.textContent = codePoints.slice(start, start + note.sectionSize).join("");
  };
  sections.addEventListener("change", showSection);
  showSection();

  panel.replaceChildren(
    node("h3", note.name),
    sections,
    excerpt,
    action("Generate draft cards", async () => {
      const section = Number(sections.value);
      status.textContent = "Generating cards. If the GPU worker is asleep, it may take a few minutes.";
      try {
        const result = await api("/api/notes/" + id + "/generate", { section, revision: note.revision });
        if (request === openNoteRequest) await openNote(id, section);
        status.textContent = result.inserted + " new draft cards.";
      } catch (error) {
        status.textContent = error.message;
      }
    }),
    node("h3", "Drafts"),
    ...note.drafts.map(draft => showDraft(draft))
  );
  if (!note.drafts.length) panel.append(node("p", "No drafts for this note."));
}

async function refreshDecks() {
  const previous = deckSelect.value;
  const decks = await api("/api/decks");
  deckSelect.replaceChildren(new Option("All cards", ""), ...decks.map(deck =>
    new Option(deck.name + " · " + deck.count + " cards", deck.id)
  ));
  deckSelect.value = decks.some(deck => String(deck.id) === previous) ? previous : "";
}

function cardSide(card, side) {
  const element = node("div");
  element.className = "card-side";
  // Only the server's allowlisted Anki rendering is HTML. Note cards are plain text.
  if (card.html) element.innerHTML = card[side];
  else element.textContent = card[side];
  return element;
}

function prefetchCard(card) {
  if (!card?.html) return;
  const template = node("template");
  template.innerHTML = card.front + card.back;
  const images = Array.from(template.content.querySelectorAll("img")).slice(0, 2);
  for (const image of images) {
    const preview = new Image();
    preview.src = image.getAttribute("src");
  }
}

async function loadReview(reset = true) {
  const request = ++reviewRequest;
  const practice = reviewMode.value === "practice";
  if (reset) {
    reviewPosition = 0;
    reviewQueue = [];
  }
  if (!reviewQueue.length) {
    reviewPanel.replaceChildren(node("p", "Loading cards…"));
    const params = new URLSearchParams({ batch: "1", deck: deckSelect.value });
    if (practice) {
      params.set("practice", "1");
      params.set("after", String(reviewPosition));
    }
    const cards = await api("/api/review?" + params);
    if (request !== reviewRequest) return;
    reviewQueue = cards;
  }

  const card = reviewQueue[0];
  if (!card) {
    reviewPanel.replaceChildren(node("p", practice ? "End of this deck." : "You’re caught up."));
    return;
  }
  const nextCard = async () => {
    if (request !== reviewRequest) return;
    reviewPosition = card.id;
    reviewQueue.shift();
    await loadReview(false);
  };
  const answer = node("div");
  const reveal = action("Show answer", () => {
    reveal.remove();
    answer.append(cardSide(card, "back"));
    if (card.evidence) answer.append(node("blockquote", card.evidence));
    if (practice) {
      answer.append(action("Next card", nextCard));
      return;
    }
    const rate = rating => async () => {
      const buttons = answer.querySelectorAll("button");
      for (const button of buttons) button.disabled = true;
      try {
        await api("/api/cards/" + card.id + "/review", { rating, reviews: card.reviews });
        await nextCard();
      } catch (error) {
        if (request === reviewRequest && error.message.includes("another device")) {
          await loadReview();
        }
        throw error;
      } finally {
        for (const button of buttons) button.disabled = false;
      }
    };
    answer.append(action("Again · 10 min", rate("again")), action("Good", rate("good")));
  });
  reviewPanel.replaceChildren(node("p", card.name), cardSide(card, "front"), reveal, answer);
  prefetchCard(reviewQueue[1]);
}

function refreshReview() {
  loadReview().catch(error => { status.textContent = error.message; });
}

document.getElementById("load-review").addEventListener("click", refreshReview);
deckSelect.addEventListener("change", refreshReview);
reviewMode.addEventListener("change", refreshReview);
const uploadLimit = 10_000_000_000;
const uploadChunkSize = 8 * 1024 * 1024;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingUploads = new WeakMap();

async function uploadFile(file, cancel) {
  if (!file?.size || file.size > uploadLimit) throw new Error("Choose a file of up to 10 GB.");
  let pending = pendingUploads.get(cancel);
  if (pending && pending.file !== file) {
    await api(pending.path + "/cancel", {}).catch(() => {});
    pending = null;
  }
  let offset = 0;
  if (pending) {
    try {
      const job = await api(pending.path);
      if (job.state === "uploading") offset = job.offset;
      else pending = null;
    } catch (error) {
      if (!error.message.includes("expired or was interrupted")) throw error;
      pending = null;
    }
  }
  if (!pending) {
    const { id } = await api("/api/uploads", { size: file.size });
    pending = { file, id, path: "/api/uploads/" + id };
    pendingUploads.set(cancel, pending);
  }
  const { id, path } = pending;
  const controller = new AbortController();
  cancel.onclick = () => controller.abort();
  cancel.hidden = false;
  try {
    let failures = 0;
    while (offset < file.size) {
      const end = Math.min(file.size, offset + uploadChunkSize);
      status.textContent = "Uploading… " + Math.floor(offset / file.size * 100) + "%";
      try {
        const response = await fetch(path + "/chunk?offset=" + offset, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file.slice(offset, end),
          signal: controller.signal
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "The chunk could not be uploaded.");
        if (!Number.isInteger(result.offset) || result.offset <= offset || result.offset > file.size) {
          throw new Error("The server could not confirm this chunk.");
        }
        offset = result.offset;
        failures = 0;
      } catch (error) {
        if (controller.signal.aborted) throw new Error("Upload canceled.");
        if (++failures >= 4) throw error;
        await pause(failures * 700);
      }
    }
    pendingUploads.delete(cancel);
    return { id, path };
  } catch (error) {
    if (controller.signal.aborted) {
      pendingUploads.delete(cancel);
      await api(path + "/cancel", {}).catch(() => {});
      throw error;
    }
    throw new Error(error.message + " Submit again to resume this upload.");
  } finally {
    cancel.hidden = true;
    cancel.onclick = null;
  }
}

document.getElementById("import-deck").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = new FormData(form);
  const file = document.getElementById("deck-file").files[0];
  const name = fields.get("name").trim();
  const button = form.querySelector("button[type=submit], button:not([type])");
  button.disabled = true;
  let upload;
  try {
    if (file && !file.name.toLowerCase().endsWith(".apkg") && file.size > 64 * 1024 * 1024) {
      throw new Error("Use an Anki .apkg package for decks larger than 64 MiB.");
    }
    upload = await uploadFile(file, form.querySelector(".cancel-upload"));
    await api(upload.path + "/import", { filename: file.name, name });
    status.textContent = "Importing the deck…";
    let result;
    for (let delay = 1000; ; delay = Math.min(5000, delay + 500)) {
      const job = await api(upload.path);
      if (job.state === "done") {
        result = job.result;
        break;
      }
      if (job.state !== "working") throw new Error(job.error || "Import did not finish.");
      await pause(delay);
    }
    form.reset();
    status.textContent = result.imported + " new cards, " + result.existing + " already in this deck, " +
      result.skipped + " skipped, " + result.missingMedia + " unavailable media files.";
    await refreshDecks();
    deckSelect.value = result.id;
    await loadReview();
  } catch (error) {
    if (upload) await api(upload.path + "/cancel", {}).catch(() => {});
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

const chatForm = document.getElementById("chat-form");
const chatSave = document.getElementById("chat-save");
const chatDrafts = document.getElementById("chat-drafts");
const chatFile = document.getElementById("chat-file");
const chatPages = document.getElementById("chat-pages");

function displayChatDrafts(cards) {
  chatDrafts.replaceChildren(...cards.map((card, index) => {
    const article = node("article");
    const selected = node("input");
    selected.type = "checkbox";
    selected.checked = true;
    selected.setAttribute("aria-label", "Save card " + (index + 1));
    const front = node("textarea");
    front.value = card.front;
    front.rows = 2;
    front.maxLength = 500;
    front.setAttribute("aria-label", "Question " + (index + 1));
    const back = node("textarea");
    back.value = card.back;
    back.rows = 2;
    back.maxLength = 2000;
    back.setAttribute("aria-label", "Answer " + (index + 1));
    article.append(selected, front, back);
    return article;
  }));
  chatSave.hidden = cards.length === 0;
}

function currentChatDrafts(selectedOnly = false) {
  return Array.from(chatDrafts.querySelectorAll("article"))
    .filter(article => !selectedOnly || article.querySelector("input").checked)
    .map(article => {
      const [front, back] = article.querySelectorAll("textarea");
      return { front: front.value, back: back.value };
    });
}

chatFile.addEventListener("change", () => {
  chatPages.hidden = !chatFile.files[0]?.name.toLowerCase().endsWith(".pdf");
});
document.getElementById("clear-chat-drafts").addEventListener("click", () => displayChatDrafts([]));

chatForm.addEventListener("submit", async event => {
  event.preventDefault();
  const fields = new FormData(chatForm);
  const file = chatFile.files[0];
  const button = chatForm.querySelector("button[type=submit], button:not([type])");
  const data = { directions: fields.get("directions"), source: fields.get("source").trim() };
  button.disabled = true;
  let upload;
  let textClipped = false;
  try {
    if (file?.size) {
      if (file.size > uploadLimit) throw new Error("Choose a file of up to 10 GB.");
      if (data.source) throw new Error("Use one temporary source per message.");
      const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (suffix === ".pdf") {
        data.first = Number(fields.get("first"));
        data.last = Number(fields.get("last")) || data.first;
        if (data.first < 1 || data.last < data.first || data.last - data.first >= 6) {
          throw new Error("Choose a range of up to six PDF pages.");
        }
        upload = await uploadFile(file, chatForm.querySelector(".cancel-upload"));
        data.upload = upload.id;
        data.suffix = suffix;
      } else if (suffix === ".txt" || suffix === ".md") {
        // Read only the small excerpt needed for generation, even when the file is very large.
        const prefix = file.slice(0, 3501 * 4);
        const characters = Array.from((await prefix.text()).trimStart());
        data.source = characters.slice(0, 3500).join("");
        textClipped = prefix.size < file.size || characters.length > 3500;
        if (!data.source.trim()) throw new Error("The beginning of this file has no readable text. Paste an excerpt.");
      } else {
        throw new Error("Choose a PDF, Markdown, or text file.");
      }
    }
    if (!data.source && !data.upload) data.drafts = currentChatDrafts();
    status.textContent = "Generating draft cards…";
    const result = await api("/api/chat/drafts", data);
    chatForm.reset();
    chatPages.hidden = true;
    displayChatDrafts(result.cards);
    status.textContent = result.cards.length + " drafts ready." +
      (result.clipped || textClipped ? " Used the first 3,500 characters of the source." : "");
  } catch (error) {
    if (upload) await api(upload.path + "/cancel", {}).catch(() => {});
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

chatSave.addEventListener("submit", async event => {
  event.preventDefault();
  const button = chatSave.querySelector("button");
  button.disabled = true;
  try {
    const selected = currentChatDrafts(true);
    if (!selected.length) throw new Error("Select at least one card to save.");
    const result = await api("/api/chat/save", { name: new FormData(chatSave).get("name"), cards: selected });
    for (const article of chatDrafts.querySelectorAll("article")) {
      if (article.querySelector("input").checked) article.remove();
    }
    chatSave.hidden = !chatDrafts.children.length;
    await refreshDecks();
    deckSelect.value = result.id;
    await loadReview();
    status.textContent = selected.length + " cards saved.";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.getElementById("note-file").addEventListener("change", async (event) => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  try {
    if (file.size > 640000) throw new Error("Choose a smaller text or Markdown file.");
    const body = await file.text();
    if (input.files[0] !== file) return;
    if (Array.from(body).length > 160000) throw new Error("Split this note into smaller files.");
    document.getElementById("name").value = file.name.slice(0, 512);
    document.getElementById("body").value = body;
  } catch (error) {
    status.textContent = error.message;
  }
});

document.getElementById("open-note").addEventListener("click", () =>
  openNote(select.value).catch(error => { status.textContent = error.message; }));
document.getElementById("import-note").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  try {
    await api("/api/notes", { source: "manual", name: data.get("name"), body: data.get("body") });
    const notes = await refreshNotes();
    const note = notes.find(item => item.source === "manual" && item.name === data.get("name").trim());
    select.value = note.id;
    await openNote(note.id);
    form.reset();
    status.textContent = "Note imported.";
  } catch (error) {
    status.textContent = error.message;
  }
});

Promise.all([refreshNotes(), refreshDecks(), loadReview()]).catch(error => { status.textContent = error.message; });
setInterval(() => refreshNotes().catch(() => {}), 15000);
