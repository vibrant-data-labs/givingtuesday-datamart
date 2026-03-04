import { twMerge } from 'tailwind-merge';

type BadgeVariant = 'indigo' | 'amber' | 'green' | 'zinc' | 'rose';

const variantClasses: Record<BadgeVariant, string> = {
  indigo: 'bg-primary/10 text-primary ring-primary/20',
  amber: 'bg-amber-50 text-amber-800 ring-amber-700/20',
  green: 'bg-emerald-50 text-emerald-800 ring-emerald-700/20',
  zinc: 'bg-secondary text-muted-foreground ring-border',
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
