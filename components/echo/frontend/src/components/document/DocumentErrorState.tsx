interface DocumentErrorStateProps {
  message: string;
}

export function DocumentErrorState({ message }: DocumentErrorStateProps) {
  return <div className="document-state document-state-error">{message}</div>;
}
