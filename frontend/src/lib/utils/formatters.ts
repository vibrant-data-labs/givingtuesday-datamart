export function formatEIN(ein: string): string {
  const digits = ein.replace(/\D/g, '');
  if (digits.length !== 9) return ein;
  return `${digits.slice(0, 2)}-${digits.slice(2)}`;
}

export function formatCurrency(amount: number | null | undefined): string {
  if (amount == null) return '—';
  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toLocaleString()}`;
}

export function formatCurrencyFull(amount: number | null | undefined): string {
  if (amount == null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatOrgName(name1: string | null, name2: string | null): string {
  const parts = [name1, name2].filter(Boolean);
  return parts.join(' ') || 'Unknown Organization';
}

export function formatAddress(
  address1: string | null,
  city: string | null,
  state: string | null,
  zip: string | null
): string {
  const parts = [address1, city, state && zip ? `${state} ${zip}` : (state ?? zip)].filter(Boolean);
  return parts.join(', ') || '—';
}

export function normalizeEIN(input: string): string {
  return input.replace(/\D/g, '').slice(0, 9);
}
