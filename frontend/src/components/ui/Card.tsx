import { twMerge } from 'tailwind-merge';

interface CardProps {
  children: React.ReactNode;
  className?: string;
}

export function Card({ children, className }: CardProps) {
  return (
    <div
      className={twMerge(
        'bg-card rounded-xl border border-border shadow-sm',
        className
      )}
    >
      {children}
    </div>
  );
}
