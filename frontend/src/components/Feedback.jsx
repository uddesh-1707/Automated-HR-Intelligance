export function ErrorMessage({ children }) {
  if (!children) return null;
  return <div className="alert alert-error" role="alert"><span>!</span>{children}</div>;
}

export function EmptyState({ title, children }) {
  return <div className="empty-state"><div className="empty-icon">⌕</div><h3>{title}</h3><p>{children}</p></div>;
}

export function StageBadge({ stage }) {
  return <span className={`badge stage-${stage}`}>{stage.replaceAll("_", " ")}</span>;
}

