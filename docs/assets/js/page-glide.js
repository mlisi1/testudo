// The Dashboard page: links within the page glide there, so it is clear the page didn't change.
(function () {
  "use strict";

  const onGlidePage = () => document.querySelector("figure.tx-map") !== null;
  const calm = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  let frame = null;

  function glideTo(top) {
    cancelAnimationFrame(frame);
    const from = window.scrollY;
    const distance = top - from;
    if (calm()) { window.scrollTo({ top, behavior: "instant" }); return; }
    const duration = Math.min(1900, 900 + Math.abs(distance) * 0.3);
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      window.scrollTo({ top: from + distance * ease(t), behavior: "instant" });
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  }

  // Whoever scrolls by hand takes over.
  ["wheel", "touchstart"].forEach((name) => window.addEventListener(name, () => cancelAnimationFrame(frame), { passive: true }));

  // Capturing, to get there before the theme's own instant jump.
  window.addEventListener("click", (event) => {
    // The theme rewrites every link to a full URL, so compare where it leads.
    const link = event.target.closest && event.target.closest("a[href]");
    if (!link || !link.hash || !onGlidePage()) return;
    if (link.origin !== location.origin || link.pathname !== location.pathname) return;
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    const header = document.querySelector(".md-header");
    const covered = header ? header.getBoundingClientRect().bottom : 0;
    history.replaceState(null, "", link.hash);
    glideTo(target.getBoundingClientRect().top + window.scrollY - covered - 16);
  }, true);

  function backToTopButton() {
    document.querySelectorAll(".tx-top").forEach((button) => button.remove());
    if (!onGlidePage()) return;
    const button = document.createElement("button");
    button.className = "tx-top";
    button.type = "button";
    button.textContent = "↑ Back to top";
    button.addEventListener("click", () => glideTo(0));
    document.body.appendChild(button);
    const update = () => button.classList.toggle("tx-top--shown", window.scrollY > 500);
    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  document$.subscribe(backToTopButton);
})();
