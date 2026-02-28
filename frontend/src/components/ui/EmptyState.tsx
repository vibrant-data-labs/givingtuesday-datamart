interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
}

export function EmptyState({ title, description, icon }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      {icon && (
        <div className="mb-4 text-zinc-300">{icon}</div>
      )}
      <p className="text-sm font-medium text-zinc-500">{title}</p>
      {description && (
        <p className="mt-1 text-xs text-zinc-400">{description}</p>
      )}
    </div>
  );
}
