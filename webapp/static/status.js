// Keeps the converter status (header badge, and the line under upload
// buttons) current: checks /queue every 15 seconds, and right away when the
// ↻ button is pressed. Without JavaScript the badge shows the status as of
// page load and links to /status.
(function () {
  const badge = document.querySelector(".status-badge");
  if (!badge) return;
  const dot = badge.querySelector(".dot");
  const button = badge.querySelector(".refresh");
  const shorts = document.querySelectorAll(".status-text");
  const labels = document.querySelectorAll(".queue-text");

  async function update() {
    button.disabled = true;
    try {
      const resp = await fetch(badge.dataset.url, { cache: "no-store" });
      if (resp.ok) {
        const s = await resp.json();
        shorts.forEach((el) => { el.textContent = s.short; });
        labels.forEach((el) => { el.textContent = s.label; });
        dot.className = "dot " + s.level;
      }
    } catch (e) {
      // Offline or the server is restarting: keep showing the last status.
    }
    button.disabled = false;
  }

  button.hidden = false;
  button.addEventListener("click", update);
  setInterval(update, 15000);
})();
