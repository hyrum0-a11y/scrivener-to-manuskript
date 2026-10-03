// Refreshes the "Converter: N converting, M waiting" line every few seconds.
(function () {
  var box = document.getElementById("queue");
  var text = document.getElementById("queue-text");
  if (!box || !text) return;
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function update() {
    fetch(box.dataset.url, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (q) {
        if (!q) return;
        text.textContent = (q.running || q.waiting)
          ? "Converter: " + plural(q.running, "book") + " converting, " + q.waiting + " waiting."
          : "Converter is free. Your book will start right away.";
      })
      .catch(function () {});
  }
  setInterval(update, 5000);
})();
