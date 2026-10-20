/** Shared compact tables: right-aligned tabular numbers, units in headers, optional sticky header. */
import type { ReactNode } from "react";
export interface Column<T> {
  key: string;
  header: ReactNode;
  unit?: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}
/** ``—`` for missing values, locale digits otherwise. */
export function formatNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (!Number.isFinite(value)) return value > 0 ? "+∞" : "−∞";
  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}
/** Seconds since the epoch as a local date and time. */
export function formatTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString();
}
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  sticky = false,
  selected,
  footnote,
}: {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  caption?: string;
  sticky?: boolean;
  selected?: (row: T) => boolean;
  footnote?: ReactNode;
}) {
  return (
    <div className={"ws-table-wrap" + (sticky ? " ws-sticky" : "")}>
      <table className="ws-table">
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={c.align === "right" ? "ws-num" : undefined}
              >
                {c.header}
                {c.unit && <small> · {c.unit}</small>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} aria-selected={selected?.(row) || undefined}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={c.align === "right" ? "ws-num" : undefined}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footnote && <div className="ws-footnote">{footnote}</div>}
    </div>
  );
}
