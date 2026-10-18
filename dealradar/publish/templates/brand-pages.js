"use strict";
(function () {
  var motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (motion.matches || !("IntersectionObserver" in window)) return;
  // Content stays visible without scripting or an observer callback.
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      if (!motion.matches) entry.target.classList.add("brand-reveal");
      observer.unobserve(entry.target);
    });
  }, {threshold: 0.08});
  document.querySelectorAll(".brand-deal, .brand-story, .brand-difference, .brand-reasons>div, .brand-day li")
    .forEach(function (card) { observer.observe(card); });
})();
