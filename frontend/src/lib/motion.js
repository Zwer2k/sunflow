// A running CSS animation makes the browser redraw the whole graphic 60 times a second. Instead the
// animations stay paused and a clock moves them on a few times a second, only while they are in view.
const FPS = 12;

/**
 * Drives the paused animations below the element: sets --sf-t (seconds) FPS times a second while
 * the element is visible. The CSS turns it into a negative animation-delay (see MOTION_CSS).
 */
export function animate(element) {
  let visible = false;
  const observer = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
  observer.observe(element);
  const timer = setInterval(() => {
    if (visible && !document.hidden) element.style.setProperty("--sf-t", (performance.now() / 1000).toFixed(3));
  }, 1000 / FPS);
  return () => {
    clearInterval(timer);
    observer.disconnect();
  };
}

/** Declarations for every animated element: paused, positioned by the clock; --offset staggers it. */
export const MOTION = "animation-play-state: paused !important; animation-delay: calc(var(--offset, 0s) - var(--sf-t, 0) * 1s) !important;";
