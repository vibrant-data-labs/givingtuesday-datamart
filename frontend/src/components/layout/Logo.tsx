interface LogoProps {
  className?: string;
}

export function LogoMark({ className = 'w-9 h-5 text-primary' }: LogoProps) {
  return (
    <svg
      viewBox="0 0 44 24"
      className={className}
      fill="none"
      stroke="currentColor"
      aria-hidden="true"
    >
      <path
        d="M 6 18 Q 22 0 38 18"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="6" cy="18" r="3" fill="currentColor" stroke="none" />
      <circle cx="38" cy="18" r="3" fill="currentColor" stroke="none" />
    </svg>
  );
}
