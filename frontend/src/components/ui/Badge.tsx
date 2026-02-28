import { twMerge } from 'tailwind-merge';

type BadgeVariant = 'indigo' | 'amber' | 'green' | 'zinc' | 'rose';

const variantClasses: Record<BadgeVariant, string> = {
  indigo: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20',
  amber: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  green: 'bg-green-50 text-green-700 ring-green-600/20',
  zinc: 'bg-zinc-100 text-zinc-600 ring-zinc-500/20',
  rose: 'bg-rose-50 text-rose-700 ring-rose-600/20',
};

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}

export function Badge({ children, variant = 'zinc', className }: BadgeProps) {
  return (
    <span
      className={twMerge(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        variantClasses[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
