// Sends a picked folder file by file (with each file's path inside the
// folder), skipping hidden folders like .obsidian and .git, and, if the form
// has data-only="<regex>", every file whose path doesn't match it. Without
// JavaScript the form still posts normally.
(function () {
  const form = document.getElementById("upload");
  const folder = document.getElementById("folder");
  const zip = document.getElementById("vault");
  const summary = document.getElementById("folder-summary");
  const errorBox = document.getElementById("error");
  const button = document.getElementById("submit");
  const label = button.textContent;
  const maxBytes = Number(form.dataset.maxMb) * 1024 * 1024;

  const only = form.dataset.only ? new RegExp(form.dataset.only, "i") : null;
  const wanted = (f) => !f.webkitRelativePath.split("/").some((part) => part.startsWith(".")) &&
    (!only || only.test(f.webkitRelativePath));
  const picked = () => Array.from(folder.files).filter(wanted);

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = false;
  }

  folder.addEventListener("change", () => {
    const files = picked();
    const mb = files.reduce((n, f) => n + f.size, 0) / 1024 / 1024;
    const name = files.length ? files[0].webkitRelativePath.split("/")[0] : "";
    summary.textContent = files.length
      ? `${name}: ${files.length} files, ${mb.toFixed(1)} MB`
      : "No book files found in that folder.";
    zip.value = "";
  });

  zip.addEventListener("change", () => {
    folder.value = "";
    summary.textContent = "";
  });

  form.addEventListener("submit", async (event) => {
    const files = picked();
    if (!files.length) return; // plain zip upload: let the browser post it
    event.preventDefault();
    const total = files.reduce((n, f) => n + f.size, 0);
    if (total > maxBytes) {
      showError(`That folder is ${(total / 1024 / 1024).toFixed(0)} MB, over the ${form.dataset.maxMb} MB limit. ` +
                "Move large files you don't need for the book out of the vault and try again.");
      return;
    }
    const data = new FormData();
    for (const [key, value] of new FormData(form)) {
      if (typeof value === "string") data.append(key, value);  // checkboxes and text boxes, not files
    }
    for (const f of files) data.append("files", f, f.webkitRelativePath);
    button.disabled = true;
    button.textContent = "Uploading…";
    try {
      const resp = await fetch(form.action, { method: "POST", body: data });
      if (resp.redirected) {
        location.href = resp.url;
        return;
      }
      const page = new DOMParser().parseFromString(await resp.text(), "text/html");
      const msg = page.getElementById("error");
      showError(msg && msg.textContent.trim() ? msg.textContent.trim() : `Upload failed (${resp.status}).`);
    } catch (e) {
      showError("The upload didn't go through. Check your connection and try again.");
    }
    button.disabled = false;
    button.textContent = label;
  });
})();
