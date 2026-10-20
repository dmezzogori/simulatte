/** Distribution summaries: counts and quantiles with the retained representation named. */
import type { Plain } from "../analysis/codec";
import { formatValue } from "../analysis/codec";
import type { OutputDescriptor } from "./types";
const REPRESENTATIONS: Record<string, string> = {
  exact: "Exact: every retained observation is represented",
  binned: "Binned: counts per bin, not the individual observations",
  approximate: "Approximate: a sketch or sampled summary",
};
const isRecord = (value: Plain | undefined): value is { [key: string]: Plain } =>
  value !== null && value !== undefined && typeof value === "object" && !Array.isArray(value);
function Pairs({ value }: { value: { [key: string]: Plain } }) {
  return (
    <dl className="ws-pairs">
      {Object.entries(value).map(([key, item]) => (
        <div key={key} className="af-pair">
          <dt>{key}</dt>
          <dd className={typeof item === "number" || typeof item === "bigint" ? "ws-num" : undefined}>{formatValue(item)}</dd>
        </div>
      ))}
    </dl>
  );
}
function Nested({ name, value }: { name: string; value: Plain }) {
  if (isRecord(value)) {
    const scalar = Object.values(value).every((v) => v === null || typeof v !== "object");
    return (
      <section className="af-dist-part">
        <h4>{name}</h4>
        {scalar ? (
          <Pairs value={value} />
        ) : (
          <table className="ws-table af-dist-table">
            <thead>
              <tr>
                <th scope="col">key</th>
                <th scope="col">value</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(value).map(([key, item]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{formatValue(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    );
  }
  if (Array.isArray(value)) {
    const rows = value.every(isRecord) ? (value as { [key: string]: Plain }[]) : null;
    const columns = rows ? [...new Set(rows.flatMap((row) => Object.keys(row)))] : [];
    return (
      <section className="af-dist-part">
        <h4>{name}</h4>
        {rows && columns.length ? (
          <div className="ws-table-wrap">
            <table className="ws-table af-dist-table">
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c} scope="col">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>
                    {columns.map((c) => (
                      <td key={c}>{formatValue(row[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ol className="af-dist-list">
            {value.map((item, i) => (
              <li key={i}>{formatValue(item)}</li>
            ))}
          </ol>
        )}
      </section>
    );
  }
  return (
    <dl className="ws-pairs">
      <div className="af-pair">
        <dt>{name}</dt>
        <dd>{formatValue(value)}</dd>
      </div>
    </dl>
  );
}
/** ``distribution.json`` after untagging: ``{summary: {representation, ...}, dataset_ref}``. */
export function DistributionSummary({
  output,
  value,
  tableLink,
}: {
  output: OutputDescriptor;
  value: Plain;
  tableLink?: (name: string) => string | null;
}) {
  if (!isRecord(value) || !isRecord(value.summary))
    return <p role="alert">The saved distribution of {output.name} has an unexpected shape.</p>;
  const summary = value.summary;
  const representation = typeof summary.representation === "string" ? summary.representation : "unknown";
  const scalars: { [key: string]: Plain } = {};
  const nested: [string, Plain][] = [];
  for (const [key, item] of Object.entries(summary)) {
    if (key === "representation") continue;
    if (item !== null && typeof item === "object") nested.push([key, item]);
    else scalars[key] = item;
  }
  const ref = typeof value.dataset_ref === "string" ? value.dataset_ref : null;
  const link = ref && tableLink ? tableLink(ref) : null;
  return (
    <div className="af-distribution">
      <p>
        <span className={"ws-badge " + (representation === "exact" ? "" : "amber")}>{representation}</span>{" "}
        <small className="ws-identity">{REPRESENTATIONS[representation] ?? "Representation not declared"}</small>
      </p>
      {Object.keys(scalars).length > 0 && <Pairs value={scalars} />}
      {nested.map(([key, item]) => (
        <Nested key={key} name={key} value={item} />
      ))}
      {ref && (
        <p className="ws-identity">
          Underlying data: {link ? <a href={link}>{ref}</a> : <code>{ref}</code>}
        </p>
      )}
    </div>
  );
}
