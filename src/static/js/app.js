// FieldOps Nexus UI behaviour. Business rules live on the server; this file only enhances.
(function () {
  // Confirmation dialogs: <button data-confirm="Message" data-confirm-title="Title">
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-confirm]");
    if (!btn || btn.dataset.confirmed === "1") return;
    ev.preventDefault();
    var dlg = document.getElementById("fx-confirm");
    document.getElementById("fx-confirm-title").textContent = btn.dataset.confirmTitle || "Please confirm";
    document.getElementById("fx-confirm-text").textContent = btn.dataset.confirm;
    dlg.returnValue = "";
    dlg.addEventListener("close", function onClose() {
      dlg.removeEventListener("close", onClose);
      if (dlg.returnValue === "ok") {
        btn.dataset.confirmed = "1";
        if (btn.form && btn.form.requestSubmit) { btn.form.requestSubmit(btn); } else { btn.click(); }
      }
    });
    dlg.showModal();
  });

  // Drawer: <button data-drawer-open="id"> / <button data-drawer-close>
  document.addEventListener("click", function (ev) {
    var open = ev.target.closest("[data-drawer-open]");
    if (open) { var d = document.getElementById(open.dataset.drawerOpen); if (d) d.hidden = false; }
    if (ev.target.closest("[data-drawer-close]")) {
      var host = ev.target.closest(".fx-drawer"); if (host) host.hidden = true;
    }
  });

  // Close search results when clicking elsewhere
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest(".fx-search")) {
      var r = document.getElementById("fx-search-results"); if (r) r.innerHTML = "";
    }
  });
})();

/* Delegated handlers (replace inline onclick attributes so the Content-Security-Policy can forbid them). */
document.addEventListener("click", function (e) {
  var nav = e.target.closest("[data-fx-toggle-nav]");
  if (nav) { var l = document.getElementById("fx-layout"); if (l) l.classList.toggle("fx-nav-open"); return; }
  if (e.target.closest("[data-fx-print]")) window.print();
});

/* Asset form: choosing another category reloads the page with the typed values so the category's custom attributes
   appear (the server builds and validates those fields). */
document.addEventListener("change", function (e) {
  var sel = e.target.closest("select[data-category-reload]");
  if (!sel || !sel.form) return;
  var params = new URLSearchParams();
  new FormData(sel.form).forEach(function (value, key) {
    if (typeof value === "string" && key !== "csrfmiddlewaretoken" && key.indexOf("attr_") !== 0 && value !== "") {
      params.set(key, value);
    }
  });
  window.location.search = params.toString();
});
