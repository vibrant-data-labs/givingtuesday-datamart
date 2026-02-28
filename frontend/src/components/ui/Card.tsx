import { twMerge } from 'tailwind-merge';

interface CardProps {
  children: React.ReactNode;
  className?: string;
}

export function Card({ children, className }: CardProps) {
  return (
    <div
      className={twMerge(
        'bg-white rounded-xl ring-1 ring-zinc-200 shadow-sm',
        className
      )}
    >
      {children}
    </div>
  );
}
