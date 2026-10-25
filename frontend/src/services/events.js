import { useEffect, useRef } from "react";

export const DATA_EVENT = "hrms:data-changed";

/** Tell every mounted page that persisted data (schedule, statuses, jobs) changed so it reloads from the API. */
export function emitDataChanged(kind = "any") {
  window.dispatchEvent(new CustomEvent(DATA_EVENT, { detail: { kind } }));
}

/** Reload from the server on data-change events, when the tab regains focus, and optionally on an interval. */
export function useDataRefresh(callback, { interval = 0 } = {}) {
  const saved = useRef(callback);
  useEffect(() => { saved.current = callback; });
  useEffect(() => {
    const run = () => saved.current();
    const onVisible = () => { if (document.visibilityState === "visible") run(); };
    window.addEventListener(DATA_EVENT, run);
    document.addEventListener("visibilitychange", onVisible);
    const timer = interval ? window.setInterval(run, interval) : null;
    return () => {
      window.removeEventListener(DATA_EVENT, run);
      document.removeEventListener("visibilitychange", onVisible);
      if (timer) window.clearInterval(timer);
    };
  }, [interval]);
}
