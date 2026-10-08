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

/* Work-order HTMX actions report success/error through server-triggered events. */
document.body.addEventListener("workOrderChanged", function (event) {
  showWorkOrderMessage(event.detail && event.detail.message || "Work order updated.", false);
});
document.body.addEventListener("workOrderActionError", function (event) {
  showWorkOrderMessage(event.detail && event.detail.message || "The work-order action failed. Refresh and retry.", true);
});
function showWorkOrderMessage(message, isError) {
  var host = document.getElementById("main");
  if (!host) return;
  var notice = document.createElement("div");
  notice.className = isError ? "wo-live-message is-error" : "wo-live-message";
  notice.setAttribute("role", isError ? "alert" : "status");
  notice.textContent = message;
  host.prepend(notice);
  window.setTimeout(function () { notice.remove(); }, 6000);
}

/* Interactive Hierarchy Hub & Tree Controls */
document.addEventListener("DOMContentLoaded", function () {
  // Tree Node Expand / Collapse Toggle
  document.addEventListener("click", function (e) {
    var expander = e.target.closest(".fx-tree-expander");
    if (!expander) return;
    var nodeItem = expander.closest(".fx-tree-node-item");
    if (!nodeItem) return;
    var branch = nodeItem.querySelector(":scope > .fx-tree-branch");
    if (!branch) return;

    var isHidden = branch.style.display === "none";
    branch.style.display = isHidden ? "" : "none";
    expander.classList.toggle("collapsed", !isHidden);
    expander.textContent = isHidden ? "▼" : "▶";
  });

  // Expand All / Collapse All / Toggle Density
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var action = btn.dataset.action;
    var wrapper = document.querySelector(".fx-hierarchy-wrapper");
    if (!wrapper) return;

    if (action === "toggle-density") {
      wrapper.classList.toggle("is-compact");
    } else if (action === "expand-all") {
      wrapper.querySelectorAll(".fx-tree-branch").forEach(function (b) { b.style.display = ""; });
      wrapper.querySelectorAll(".fx-tree-expander").forEach(function (exp) {
        if (!exp.classList.contains("opacity-25")) {
          exp.classList.remove("collapsed");
          exp.textContent = "▼";
        }
      });
    } else if (action === "collapse-all") {
      wrapper.querySelectorAll(".fx-tree-branch").forEach(function (b) { b.style.display = "none"; });
      wrapper.querySelectorAll(".fx-tree-expander").forEach(function (exp) {
        if (!exp.classList.contains("opacity-25")) {
          exp.classList.add("collapsed");
          exp.textContent = "▶";
        }
      });
    }
  });

  // Role Filtering Pills
  document.addEventListener("click", function (e) {
    var pill = e.target.closest("[data-filter-role]");
    if (!pill) return;
    var group = pill.closest(".fx-filter-pill-group");
    if (!group) return;
    group.querySelectorAll(".fx-filter-pill").forEach(function (p) { p.classList.remove("active"); });
    pill.classList.add("active");

    var role = pill.dataset.filterRole; // 'all', 'assembly', 'component', 'replaceable'
    var cards = document.querySelectorAll(".fx-tree-node-card");
    cards.forEach(function (card) {
      var item = card.closest(".fx-tree-node-item");
      if (!item) return;
      if (role === "all") {
        item.style.opacity = "";
      } else if (role === "assembly" && card.classList.contains("role-assembly")) {
        item.style.opacity = "";
      } else if (role === "component" && card.classList.contains("role-component")) {
        item.style.opacity = "";
      } else if (role === "replaceable" && (card.classList.contains("role-replaceable_part") || card.classList.contains("role-replaceable"))) {
        item.style.opacity = "";
      } else if (card.classList.contains("role-root")) {
        item.style.opacity = "";
      } else {
        item.style.opacity = "0.3";
      }
    });
  });

  // Live Tree Search Filtering
  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-tree-search]");
    if (!input) return;
    var query = input.value.trim().toLowerCase();
    var nodeItems = document.querySelectorAll(".fx-tree-node-item");

    if (!query) {
      nodeItems.forEach(function (item) { item.style.display = ""; });
      return;
    }

    nodeItems.forEach(function (item) {
      var card = item.querySelector(":scope > .fx-tree-node-card");
      var text = card ? card.textContent.toLowerCase() : "";
      if (text.indexOf(query) !== -1) {
        item.style.display = "";
        // ensure ancestors are visible
        var parent = item.parentElement.closest(".fx-tree-node-item");
        while (parent) {
          parent.style.display = "";
          var parentBranch = parent.querySelector(":scope > .fx-tree-branch");
          if (parentBranch) parentBranch.style.display = "";
          parent = parent.parentElement.closest(".fx-tree-node-item");
        }
      } else {
        var hasMatchingChild = item.querySelector(".fx-tree-branch .fx-tree-node-item");
        // if children match, keep open
        var childText = item.querySelector(".fx-tree-branch") ? item.querySelector(".fx-tree-branch").textContent.toLowerCase() : "";
        if (childText.indexOf(query) !== -1) {
          item.style.display = "";
        } else {
          item.style.display = "none";
        }
      }
    });
  });

  // Clear Search Button
  document.addEventListener("click", function (e) {
    var clearBtn = e.target.closest(".fx-tree-search-clear");
    if (!clearBtn) return;
    var searchBox = clearBtn.closest(".fx-tree-search");
    if (!searchBox) return;
    var input = searchBox.querySelector("input");
    if (input) {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  });

  // Copy Part Number Feedback
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var text = btn.dataset.copy;
    if (navigator.clipboard && text) {
      navigator.clipboard.writeText(text).then(function () {
        var orig = btn.innerHTML;
        btn.innerHTML = "✓ Copied";
        setTimeout(function () { btn.innerHTML = orig; }, 1500);
      });
    }
  });
});
