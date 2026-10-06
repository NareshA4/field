/**
 * FieldOps Nexus — Interactive Asset Hierarchy Tree Controller (Phase 1)
 */
(function () {
  'use strict';

  function initTree(wrapper) {
    if (!wrapper || wrapper.dataset.treeInitialized === 'true') return;
    wrapper.dataset.treeInitialized = 'true';

    // 1. Expand / Collapse Branch Click Handlers
    wrapper.addEventListener('click', function (e) {
      const expander = e.target.closest('.fx-tree-expander');
      if (!expander) return;

      e.preventDefault();
      const nodeItem = expander.closest('.fx-tree-node-item');
      if (!nodeItem) return;

      const branch = nodeItem.querySelector(':scope > .fx-tree-branch');
      if (!branch) return;

      const isCollapsed = expander.classList.toggle('is-collapsed');
      if (isCollapsed) {
        branch.style.display = 'none';
        expander.setAttribute('aria-expanded', 'false');
      } else {
        branch.style.display = '';
        expander.setAttribute('aria-expanded', 'true');
      }
    });

    // 2. Expand All / Collapse All Controls
    const expandAllBtn = wrapper.querySelector('[data-action="expand-all"]');
    const collapseAllBtn = wrapper.querySelector('[data-action="collapse-all"]');

    if (expandAllBtn) {
      expandAllBtn.addEventListener('click', function () {
        wrapper.querySelectorAll('.fx-tree-expander').forEach(function (btn) {
          btn.classList.remove('is-collapsed');
          btn.setAttribute('aria-expanded', 'true');
        });
        wrapper.querySelectorAll('.fx-tree-branch').forEach(function (branch) {
          branch.style.display = '';
        });
      });
    }

    if (collapseAllBtn) {
      collapseAllBtn.addEventListener('click', function () {
        wrapper.querySelectorAll('.fx-tree-expander').forEach(function (btn) {
          btn.classList.add('is-collapsed');
          btn.setAttribute('aria-expanded', 'false');
        });
        wrapper.querySelectorAll('.fx-tree-branch').forEach(function (branch) {
          branch.style.display = 'none';
        });
      });
    }

    // 3. Density Toggler (Compact vs Comfortable)
    const densityBtn = wrapper.querySelector('[data-action="toggle-density"]');
    if (densityBtn) {
      densityBtn.addEventListener('click', function () {
        const isCompact = wrapper.classList.toggle('fx-hierarchy-compact');
        densityBtn.classList.toggle('active', isCompact);
        try {
          localStorage.setItem('fx_tree_density', isCompact ? 'compact' : 'standard');
        } catch (err) {}
      });
      if (localStorage.getItem('fx_tree_density') === 'compact') {
        wrapper.classList.add('fx-hierarchy-compact');
        densityBtn.classList.add('active');
      }
    }

    // 4. Role Filter Buttons (All, Assemblies, Components, Replaceable Parts)
    const filterPills = wrapper.querySelectorAll('.fx-filter-pill[data-filter-role]');
    filterPills.forEach(function (pill) {
      pill.addEventListener('click', function () {
        filterPills.forEach(function (p) { p.classList.remove('active'); });
        this.classList.add('active');
        const role = this.dataset.filterRole;

        const cards = wrapper.querySelectorAll('.fx-tree-node-card');
        const items = wrapper.querySelectorAll('.fx-tree-node-item');

        if (!role || role === 'all') {
          cards.forEach(function (c) { c.classList.remove('is-matched'); });
          items.forEach(function (it) { it.style.display = ''; });
          return;
        }

        cards.forEach(function (card) {
          const hasRole = card.classList.contains('role-' + role);
          if (hasRole) {
            card.classList.add('is-matched');
            let parent = card.closest('.fx-tree-node-item');
            while (parent) {
              parent.style.display = '';
              const branch = parent.closest('.fx-tree-branch');
              if (branch) {
                branch.style.display = '';
                const exp = branch.closest('.fx-tree-node-item')?.querySelector(':scope > .fx-tree-node-card .fx-tree-expander');
                if (exp) {
                  exp.classList.remove('is-collapsed');
                  exp.setAttribute('aria-expanded', 'true');
                }
              }
              parent = branch ? branch.closest('.fx-tree-node-item') : null;
            }
          } else {
            card.classList.remove('is-matched');
          }
        });
      });
    });

    // 5. Fast Client-side Tree Search
    const searchInput = wrapper.querySelector('[data-tree-search]');
    const clearBtn = wrapper.querySelector('.fx-tree-search-clear');

    if (searchInput) {
      function runSearch() {
        const query = searchInput.value.trim().toLowerCase();
        if (clearBtn) clearBtn.style.display = query ? 'block' : 'none';

        const cards = wrapper.querySelectorAll('.fx-tree-node-card');
        const nodeItems = wrapper.querySelectorAll('.fx-tree-node-item');

        if (!query) {
          cards.forEach(function (c) { c.classList.remove('is-matched'); });
          nodeItems.forEach(function (item) { item.style.display = ''; });
          return;
        }

        cards.forEach(function (card) {
          const text = card.textContent.toLowerCase();
          const match = text.includes(query);
          if (match) {
            card.classList.add('is-matched');
            let parent = card.closest('.fx-tree-node-item');
            while (parent) {
              parent.style.display = '';
              const branch = parent.closest('.fx-tree-branch');
              if (branch) {
                branch.style.display = '';
                const exp = branch.closest('.fx-tree-node-item')?.querySelector(':scope > .fx-tree-node-card .fx-tree-expander');
                if (exp) {
                  exp.classList.remove('is-collapsed');
                  exp.setAttribute('aria-expanded', 'true');
                }
              }
              parent = branch ? branch.closest('.fx-tree-node-item') : null;
            }
          } else {
            card.classList.remove('is-matched');
          }
        });
      }

      searchInput.addEventListener('input', runSearch);

      if (clearBtn) {
        clearBtn.addEventListener('click', function () {
          searchInput.value = '';
          runSearch();
          searchInput.focus();
        });
      }
    }

    // Keyboard Accessibility for Expanders
    wrapper.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        const expander = e.target.closest('.fx-tree-expander');
        if (expander) {
          e.preventDefault();
          expander.click();
        }
      }
    });
  }

  // Global Clipboard Handler for any [data-copy] element
  document.addEventListener('click', function (e) {
    const copyBtn = e.target.closest('[data-copy]');
    if (!copyBtn) return;
    const text = copyBtn.dataset.copy;
    if (!text) return;

    navigator.clipboard.writeText(text).then(function () {
      const originalHtml = copyBtn.innerHTML;
      copyBtn.innerText = 'Copied!';
      setTimeout(function () {
        copyBtn.innerHTML = originalHtml;
      }, 1500);
    }).catch(function () {
      // Fallback
    });
  });

  // Auto-init on page load and HTMX content swaps
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.fx-hierarchy-wrapper').forEach(initTree);
  });

  if (window.htmx) {
    document.body.addEventListener('htmx:afterSwap', function (e) {
      if (e.detail.target) {
        const wrappers = e.detail.target.matches('.fx-hierarchy-wrapper')
          ? [e.detail.target]
          : e.detail.target.querySelectorAll('.fx-hierarchy-wrapper');
        wrappers.forEach(initTree);
      }
    });
  }
})();
