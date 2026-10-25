import { createContext, useCallback, useContext, useMemo, useState } from "react";

const noop = () => {};
const ToastContext = createContext({ success: noop, error: noop, info: noop });

export function useToast() {
  return useContext(ToastContext);
}

/** Lightweight confirmation toasts so every action gets visible feedback. */
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const dismiss = useCallback(id => setToasts(current => current.filter(item => item.id !== id)), []);
  const push = useCallback((kind, message, duration = 6000) => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(current => [...current.slice(-3), { id, kind, message }]);
    window.setTimeout(() => dismiss(id), duration);
  }, [dismiss]);
  const api = useMemo(() => ({
    success: message => push("success", message),
    info: message => push("info", message),
    error: message => push("error", message, 9000)
  }), [push]);
  const icons = { success: "✓", error: "!", info: "i" };
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map(item => (
          <div key={item.id} className={`toast toast-${item.kind}`} role={item.kind === "error" ? "alert" : "status"}>
            <span className="toast-icon" aria-hidden="true">{icons[item.kind]}</span>
            <p>{item.message}</p>
            <button type="button" aria-label="Dismiss notification" onClick={() => dismiss(item.id)}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
