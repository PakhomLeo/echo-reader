interface DocumentEmptyStateProps {
  message: string;
}

export function DocumentEmptyState({ message }: DocumentEmptyStateProps) {
  return <div className="document-state">{message}</div>;
}
