const params = new URLSearchParams(location.search);
const deck = params.get("deck") || "";
const practice = params.get("mode") === "practice";
const stage = document.getElementById("review-stage");
const cardPanel = document.getElementById("review-card");
const face = document.getElementById("card-face");
const progress = document.getElementById("session-progress");
const title = document.getElementById("session-title");
const modeSwitch = document.getElementById("session-switch");
const reveal = document.getElementById("show-answer");
const hide = document.getElementById("show-question");
const ratingActions = document.getElementById("rating-actions");
const ratings = Array.from(ratingActions.querySelectorAll("button"));
const next = document.getElementById("next-card");
const retry = document.getElementById("retry-review");
let queue = [];
let after = 0;
let completed = 0;
let total = 0;
let pending = false;
let wakeTimer;

function cardSide(card, side) {
  const element = node("div");
  element.className = card.html ? "card-side rich-card" : "card-side";
  // The importer strips untrusted markup. Generated cards remain plain text.
  if (card.html) element.innerHTML = card[side];
  else element.textContent = card[side];
  return element;
}

function intervalLabel(seconds) {
  if (seconds >= 86400) return (seconds / 86400) + (seconds === 86400 ? " day" : " days");
  return Math.ceil(seconds / 60) + " min";
}

function showFace(answer, moveFocus = true) {
  const card = queue[0];
  if (!card) return;
  face.hidden = false;
  face.textContent = (answer ? "Answer" : "Question") + (deck ? "" : " · " + card.name);
  cardPanel.replaceChildren(cardSide(card, answer ? "back" : "front"));
  if (answer && card.evidence) cardPanel.append(node("blockquote", card.evidence));
  stage.scrollTop = 0;
  reveal.hidden = answer;
  hide.hidden = !answer;
  next.hidden = !answer || !practice;
  ratingActions.hidden = !answer || practice;
  if (!ratingActions.hidden) {
    for (const button of ratings) {
      button.querySelector("small").textContent = intervalLabel(card.intervals[button.dataset.rating]);
    }
  }
  if (moveFocus) (answer ? stage : reveal).focus({ preventScroll: true });
}

function showMessage(message) {
  face.hidden = reveal.hidden = hide.hidden = ratingActions.hidden = next.hidden = true;
  cardPanel.replaceChildren(node("p", message));
}

function prefetchQuestion(card) {
  if (!card?.html) return;
  const template = node("template");
  template.innerHTML = card.front;
  const image = template.content.querySelector("img");
  if (image) new Image().src = image.getAttribute("src");
}

async function loadCard(moveFocus = false) {
  clearTimeout(wakeTimer);
  retry.hidden = true;
  let wait;
  if (!queue.length) {
    showMessage("Loading cards…");
    const query = new URLSearchParams({ batch: "1", session: "1", deck });
    if (practice) {
      query.set("practice", "1");
      query.set("after", after);
    }
    const result = await api("/api/review?" + query);
    queue = result.cards;
    wait = result.wait;
  }
  const card = queue[0];
  if (!card) {
    progress.textContent = completed ? completed + (practice ? " browsed" : " reviewed") : "";
    showMessage(practice ? "End of this deck." : "You’re caught up.");
    if (!practice) {
      retry.textContent = "Check for due cards";
      retry.hidden = false;
      if (wait > 0 && wait <= 15 * 60) {
        cardPanel.append(node("p", "Next card in about " + intervalLabel(wait) + "."));
        wakeTimer = setTimeout(() => start(), wait * 1000);
      }
    }
    if (moveFocus) (practice ? stage : retry).focus({ preventScroll: true });
    return;
  }
  progress.textContent = practice
    ? "Card " + (completed + 1).toLocaleString() + (total ? " of " + total.toLocaleString() : "")
    : completed.toLocaleString() + " reviewed this session";
  showFace(false, moveFocus);
  prefetchQuestion(queue[1]);
}

async function loadOrRetry(moveFocus) {
  try {
    await loadCard(moveFocus);
  } catch (error) {
    showMessage("Couldn’t load cards.");
    status.textContent = error.message;
    retry.textContent = "Try again";
    retry.hidden = false;
    if (moveFocus) retry.focus({ preventScroll: true });
  }
}

async function advance(rating) {
  if (pending || !queue.length) return;
  pending = true;
  status.textContent = "";
  const card = queue[0];
  const controls = [hide, ...ratings, next];
  for (const button of controls) button.disabled = true;
  try {
    if (!practice) await api("/api/cards/" + card.id + "/review", { rating, reviews: card.reviews });
    after = card.id;
    completed++;
    queue.shift();
    await loadOrRetry(true);
  } catch (error) {
    if (error.message.includes("another device")) {
      queue = [];
      await loadOrRetry(true);
    }
    status.textContent = error.message;
  } finally {
    for (const button of controls) button.disabled = false;
    pending = false;
  }
}

async function start(moveFocus = false) {
  status.textContent = "";
  if (deck && modeSwitch.hidden) {
    try {
      const decks = await api("/api/decks");
      const selected = decks.find(item => String(item.id) === deck);
      if (!selected) {
        retry.hidden = true;
        showMessage("This deck is unavailable. Choose another from Decks.");
        return;
      }
      title.textContent = selected.name;
      total = selected.count;
    } catch (error) {
      showMessage("Couldn’t load the deck.");
      status.textContent = error.message;
      retry.textContent = "Try again";
      retry.hidden = false;
      return;
    }
  }
  modeSwitch.hidden = false;
  await loadOrRetry(moveFocus);
}

document.getElementById("session-mode").textContent = practice ? "Browse cards" : "Due review";
title.textContent = deck ? "Study session" : "All cards";
modeSwitch.href = studyLink(deck, !practice);
modeSwitch.textContent = practice ? "Review due" : "Browse all";
reveal.addEventListener("click", () => showFace(true));
hide.addEventListener("click", () => showFace(false));
next.addEventListener("click", () => advance());
for (const button of ratings) {
  button.addEventListener("click", () => advance(button.dataset.rating));
}
retry.addEventListener("click", async () => {
  retry.disabled = true;
  await start(true);
  retry.disabled = false;
  if (!retry.hidden) retry.focus({ preventScroll: true });
});

document.addEventListener("keydown", event => {
  if (event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (event.target.closest("input, textarea, select, audio, [contenteditable]")) return;
  let button;
  if (event.key === " " && !event.target.closest("button, a")) button = reveal;
  if (!ratingActions.hidden && /^[1-4]$/.test(event.key)) button = ratings[Number(event.key) - 1];
  if (event.key === "ArrowRight") button = next;
  if (button && !button.hidden && !button.disabled) {
    event.preventDefault();
    button.click();
  }
});

start();
