/**
 * FieldOps Nexus — Public Marketing Interactivity (Vanilla JS)
 * High-performance 60fps animations, mega-menu interactions, and scroll telemetry.
 * Fully compliant with strict CSP (No inline scripts or handlers).
 */

document.addEventListener('DOMContentLoaded', () => {
  // =========================================================================
  // 1. Top Edge Gold Scroll Signal & NexusScrollFlow Telemetry 
  // =========================================================================
  const navScrollSignal = document.getElementById('nexus-nav-scroll-signal');
  const railLine = document.getElementById('nexus-rail-line');
  const signalHead = document.getElementById('nexus-signal-head');
  const stageTag = document.getElementById('nexus-stage-tag');

  const GLOBAL_STAGES = [
    { p: 0.00, label: "SIGNAL INITIATED" },
    { p: 0.20, label: "CONNECTED OPERATIONS" },
    { p: 0.45, label: "MODULE ECOSYSTEM" },
    { p: 0.70, label: "INTELLIGENCE & CONTROL" },
    { p: 0.92, label: "NETWORK SYNCHRONIZED" }
  ];

  const updateScrollSignals = () => {
    const scrollY = window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
    const scrollHeight = Math.max(
      document.documentElement.scrollHeight,
      document.body.scrollHeight,
      0
    );
    const clientHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const maxScroll = scrollHeight - clientHeight;
    const progress = maxScroll > 0 ? Math.min(Math.max(scrollY / maxScroll, 0), 1) : 0;

    // Top navbar indicator
    if (navScrollSignal) {
      navScrollSignal.style.transform = `scaleX(${progress})`;
    }

    // Right-rail telemetry
    if (railLine) {
      railLine.style.transform = `scaleY(${progress})`;
    }
    if (signalHead) {
      const y = progress * 220; // 220px track height
      signalHead.style.transform = `translate(-50%, -50%) translateY(${y}px)`;
    }
    if (stageTag) {
      let currentStage = GLOBAL_STAGES[0].label;
      for (let i = 0; i < GLOBAL_STAGES.length; i++) {
        if (progress >= GLOBAL_STAGES[i].p) {
          currentStage = GLOBAL_STAGES[i].label;
        }
      }
      stageTag.textContent = currentStage;
    }
  };

  window.addEventListener('scroll', updateScrollSignals, { passive: true });
  window.addEventListener('resize', updateScrollSignals, { passive: true });
  updateScrollSignals();

  // =========================================================================
  // 2. Desktop Mega-Menu Hover & Click Handling
  // =========================================================================
  const megaTriggers = document.querySelectorAll('[data-fx-mega-trigger]');
  const megaPanels = document.querySelectorAll('.fx-mega-menu');
  let closeTimer = null;
  let activeMenu = null;

  const cancelClose = () => {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
  };

  const openMega = (menuId) => {
    cancelClose();
    activeMenu = menuId;
    megaPanels.forEach(panel => {
      if (panel.id === `mega-${menuId}`) {
        panel.classList.add('is-active');
      } else {
        panel.classList.remove('is-active');
      }
    });
    megaTriggers.forEach(btn => {
      if (btn.getAttribute('data-fx-mega-trigger') === menuId) {
        btn.classList.add('is-open');
        btn.setAttribute('aria-expanded', 'true');
      } else {
        btn.classList.remove('is-open');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimer = setTimeout(() => {
      closeMega();
    }, 220);
  };

  const closeMega = () => {
    cancelClose();
    activeMenu = null;
    megaPanels.forEach(panel => panel.classList.remove('is-active'));
    megaTriggers.forEach(btn => {
      btn.classList.remove('is-open');
      btn.setAttribute('aria-expanded', 'false');
    });
  };

  megaTriggers.forEach(btn => {
    const menuId = btn.getAttribute('data-fx-mega-trigger');

    btn.addEventListener('mouseenter', () => openMega(menuId));
    btn.addEventListener('mouseleave', scheduleClose);

    btn.addEventListener('click', (e) => {
      e.preventDefault();
      cancelClose();
      if (activeMenu === menuId) {
        closeMega();
      } else {
        openMega(menuId);
      }
    });
  });

  megaPanels.forEach(panel => {
    panel.addEventListener('mouseenter', cancelClose);
    panel.addEventListener('mouseleave', scheduleClose);
  });

  // Close on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.fx-pub-header') && !e.target.closest('.fx-mega-menu')) {
      closeMega();
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeMega();
      closeMobileNav();
      closeChat();
    }
  });

  // =========================================================================
  // 3. Mobile Navigation Drawer & Accordions
  // =========================================================================
  const mobileToggle = document.querySelector('[data-fx-pub-toggle]');
  const mobileDrawer = document.querySelector('[data-fx-pub-drawer]');

  const closeMobileNav = () => {
    if (mobileDrawer && mobileToggle) {
      mobileDrawer.classList.remove('is-open');
      mobileToggle.setAttribute('aria-expanded', 'false');
    }
  };

  if (mobileToggle && mobileDrawer) {
    mobileToggle.addEventListener('click', () => {
      const isOpen = mobileDrawer.classList.contains('is-open');
      if (isOpen) {
        closeMobileNav();
      } else {
        mobileDrawer.classList.add('is-open');
        mobileToggle.setAttribute('aria-expanded', 'true');
      }
    });
  }

  // Mobile Accordion Triggers
  document.querySelectorAll('[data-fx-mobile-accordion]').forEach(accBtn => {
    accBtn.addEventListener('click', () => {
      const targetId = accBtn.getAttribute('data-fx-mobile-accordion');
      const content = document.getElementById(targetId);
      if (content) {
        const isOpen = content.classList.contains('is-open');
        // Close siblings
        document.querySelectorAll('.fx-mobile-accordion-content').forEach(c => c.classList.remove('is-open'));
        document.querySelectorAll('[data-fx-mobile-accordion]').forEach(b => b.classList.remove('is-active'));
        if (!isOpen) {
          content.classList.add('is-open');
          accBtn.classList.add('is-active');
        }
      }
    });
  });

  // =========================================================================
  // 4. 7-Step Interactive Workflow Timeline (Scroll-Synchronized)
  // =========================================================================
  const workflowSection = document.getElementById('workflow');
  const workflowLineFill = document.getElementById('workflow-line-fill');
  const workflowSteps = document.querySelectorAll('.fx-timeline-step');

  if (workflowSection && workflowLineFill && workflowSteps.length > 0) {
    let ticking = false;

    const handleWorkflowScroll = () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          const rect = workflowSection.getBoundingClientRect();
          const vh = window.innerHeight;

          const startOffset = vh * 0.70;
          const endOffset = vh * 0.25;
          const totalDistance = rect.height + (startOffset - endOffset);
          const currentDistance = startOffset - rect.top;

          const rawProgress = currentDistance / totalDistance;
          const clamped = Math.min(Math.max(rawProgress, 0), 1);

          const isDesktop = window.innerWidth >= 768;
          if (isDesktop) {
            workflowLineFill.style.transform = `scaleX(${clamped})`;
          } else {
            workflowLineFill.style.transform = `scaleY(${clamped})`;
          }

          // Active step index (0 to 6)
          const activeIndex = Math.min(Math.max(Math.floor(clamped * 6 + 0.5), 0), 6);

          workflowSteps.forEach((step, idx) => {
            if (idx === activeIndex) {
              step.classList.add('active');
              step.classList.remove('completed');
            } else if (idx < activeIndex) {
              step.classList.remove('active');
              step.classList.add('completed');
            } else {
              step.classList.remove('active', 'completed');
            }
          });

          ticking = false;
        });
        ticking = true;
      }
    };

    window.addEventListener('scroll', handleWorkflowScroll, { passive: true });
    window.addEventListener('resize', handleWorkflowScroll, { passive: true });
    handleWorkflowScroll();
  }

  // =========================================================================
  // 5. Floating Concierge Chat FAB & Modal
  // =========================================================================
  const chatFab = document.querySelector('[data-fx-chat-fab]');
  const chatModal = document.querySelector('[data-fx-chat-modal]');
  const chatClose = document.querySelector('[data-fx-chat-close]');

  const closeChat = () => {
    if (chatModal) {
      chatModal.classList.remove('is-open');
    }
  };

  if (chatFab && chatModal) {
    chatFab.addEventListener('click', () => {
      chatModal.classList.toggle('is-open');
    });
  }
  if (chatClose) {
    chatClose.addEventListener('click', closeChat);
  }

  // =========================================================================
  // 6. Reveal On Scroll Observer
  // =========================================================================
  const revealElements = document.querySelectorAll('.landing-reveal');
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -20px 0px' });

    revealElements.forEach(el => observer.observe(el));
  } else {
    revealElements.forEach(el => el.classList.add('is-visible'));
  }

  // =========================================================================
  // 7. Video Autoplay Fallback Verification
  // =========================================================================
  const showcaseVideo = document.querySelector('.fx-showcase-video');
  if (showcaseVideo) {
    const playPromise = showcaseVideo.play();
    if (playPromise !== undefined) {
      playPromise.catch(() => {
        // Autoplay muted was prevented or user preferred reduced data
      });
    }
  }

  // =========================================================================
  // 8. Smooth Anchor Scrolling with 76px Header Offset
  // =========================================================================
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId && targetId !== '#') {
        const targetEl = document.querySelector(targetId);
        if (targetEl) {
          e.preventDefault();
          closeMega();
          closeMobileNav();
          const navH = 76;
          const top = targetEl.getBoundingClientRect().top + window.scrollY - navH;
          window.scrollTo({ top, behavior: 'smooth' });
        }
      }
    });
  });

  // =========================================================================
  // 9. About Page: Five Core Principles Interactive Feature Cards
  // =========================================================================
  const principleCards = document.querySelectorAll('.fx-principle-card');
  if (principleCards.length > 0) {
    principleCards.forEach((card) => {
      // Remove is-pulsing after animation ends so it doesn't clash with hover/selected styles
      card.addEventListener('animationend', (e) => {
        if (e.animationName === 'fx-card-gold-pulse') {
          card.classList.remove('is-pulsing');
        }
      });

      const activateCard = (e) => {
        if (e && e.type === 'keydown') {
          e.preventDefault();
        }

        // Unset other cards
        principleCards.forEach((other) => {
          if (other !== card) {
            other.classList.remove('is-selected', 'is-pulsing');
            other.setAttribute('aria-pressed', 'false');
          }
        });

        // Set selected state
        card.classList.add('is-selected');
        card.setAttribute('aria-pressed', 'true');

        // Replay click pulse animation cleanly
        card.classList.remove('is-pulsing');
        void card.offsetWidth; // Force reflow
        card.classList.add('is-pulsing');
      };

      card.addEventListener('click', activateCard);
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          activateCard(e);
        }
      });
    });
  }
});
