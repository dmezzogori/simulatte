/** Optional backend metadata is presentation; it never infers actor replay. */
import type {ExecutionProvenance} from '@simulatte/trace';

function map(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function supportsFullReplay(execution: ExecutionProvenance | null): boolean {
  const resolved = map(execution?.initial?.['resolved']);
  const backend = map(map(resolved?.['capabilities'])?.['execution_backend']);
  const levels = backend?.['trace_levels'];
  return !Array.isArray(levels) || levels.includes('full');
}

export function backendObservation(execution: ExecutionProvenance | null): string | null {
  const resolved = map(execution?.initial?.['resolved']);
  const backend = map(map(resolved?.['capabilities'])?.['execution_backend']);
  if (!backend || backend['schema'] !== 1 || typeof backend['name'] !== 'string' ||
      !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(backend['name'])) return null;
  const name = backend['name'] === 'native-production' ? 'Native production' : backend['name'];
  const label = name + (backend['experimental'] === true ? ' · experimental' : '');
  const observation = map(backend['observation']);
  if (!observation || observation['schema'] !== 1 || observation['kind'] !== 'scalar_snapshots' ||
      observation['boundary'] !== 'right_open') return label + ' · final KPI scalars';
  const interval = observation['effective_interval'], requested = observation['requested_interval'];
  const count = observation['scheduled_samples'], limit = observation['max_samples'];
  if (!finite(interval) || interval <= 0 || !finite(requested) || requested <= 0 || !finite(limit) ||
      !Number.isInteger(limit) || limit < 2 || limit > 4096 || !finite(count) || !Number.isInteger(count) || count < 2 || count > limit) return label;
  const cadence = interval.toLocaleString('en', {maximumSignificantDigits: 5});
  return `${label} · KPI snapshots every ${cadence} time units · ${count} samples per KPI${interval > requested ? ' (spacing increased to fit limit)' : ''}`;
}
