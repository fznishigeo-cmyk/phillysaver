function toolObserveMeters() {
  var meters = document.querySelectorAll('.gauge:not([data-view])');
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
    meters.forEach(meter => meter.setAttribute('data-view', ''));
    return;
  }
  if (!window.toolMeterObserver) window.toolMeterObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      requestAnimationFrame(() => entry.target.setAttribute('data-view', ''));
      window.toolMeterObserver.unobserve(entry.target);
    });
  });
  meters.forEach(meter => window.toolMeterObserver.observe(meter));
}
toolObserveMeters();
