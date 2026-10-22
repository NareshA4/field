/**
 * FieldOps Nexus — Public Marketing Interactivity (Vanilla JS)
 * Handles mobile nav toggle, video play triggers, and workflow tab switching.
 * Compliant with strict CSP (No inline event handlers).
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Mobile Menu Toggle
  const mobileToggle = document.querySelector('[data-fx-pub-toggle]');
  const mobileNav = document.querySelector('[data-fx-pub-nav]');

  if (mobileToggle && mobileNav) {
    mobileToggle.addEventListener('click', () => {
      const isExpanded = mobileToggle.getAttribute('aria-expanded') === 'true';
      mobileToggle.setAttribute('aria-expanded', !isExpanded);
      mobileNav.classList.toggle('d-none');
    });
  }

  // 2. Interactive Workflow Tabs
  const workflowTabs = document.querySelectorAll('[data-fx-workflow-tab]');
  const workflowPanels = document.querySelectorAll('[data-fx-workflow-panel]');

  if (workflowTabs.length > 0 && workflowPanels.length > 0) {
    workflowTabs.forEach((tab) => {
      tab.addEventListener('click', (e) => {
        e.preventDefault();
        const targetId = tab.getAttribute('data-fx-workflow-tab');

        workflowTabs.forEach((t) => t.classList.remove('active', 'border-fx-gold'));
        workflowPanels.forEach((p) => p.classList.add('d-none'));

        tab.classList.add('active', 'border-fx-gold');
        const activePanel = document.querySelector(`[data-fx-workflow-panel="${targetId}"]`);
        if (activePanel) {
          activePanel.classList.remove('d-none');
        }
      });
    });
  }

  // 3. Smooth Anchor Scrolling
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId && targetId !== '#') {
        const targetEl = document.querySelector(targetId);
        if (targetEl) {
          e.preventDefault();
          targetEl.scrollIntoView({ behavior: 'smooth' });
        }
      }
    });
  });
});
