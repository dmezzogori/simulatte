import {useEffect, useRef, useState} from 'react';
import {MAX_OVERLAY, OverlayChart, seriesInputs} from './overlay-charts';
import {WelchModel, finitePoints, uniformGrid} from './welch';
import type {ExperimentReport, ReplicationEntry, SeriesEntry, StudioClient} from './studio-client';

const BATCH = 8;            // series.get takes at most eight recordings per call.
const WELCH_REPLICATIONS = 64;
const WELCH_GRID = 500;
const committed = (item: ReplicationEntry) => (item.status === 'succeeded' || item.status === 'reused') && item.attempt !== null;
const LISTED = 1000;        // ExperimentReport.to_dict lists at most this many replications.

/** The committed attempts a report lists for one configuration, or why there are none. */
export function welchAttempts(report: Pick<ExperimentReport, 'configurations' | 'replications' | 'replications_total'>, configuration: number):
    {attempts: string[]; total: number; message?: string} {
  const attempts = report.replications.filter(item => item.configuration === configuration && committed(item)).map(item => item.attempt!);
  if (attempts.length) return {attempts, total: attempts.length};
  const label = report.configurations[configuration]?.label ?? String(configuration);
  const message = report.replications.length < report.replications_total
    ? `${label} has no committed replication among the first ${report.replications.length} of ${report.replications_total} listed replications (the report lists at most ${LISTED})`
    : `${label} has no listed committed replication yet`;
  return {attempts, total: 0, message};
}

/** Overlay of selected replications and the Welch plot of one configuration, on one imperative uPlot. */
export function ExperimentCharts({client, report, selected}: {client: StudioClient; report: ExperimentReport; selected: readonly ReplicationEntry[]}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<OverlayChart | null>(null);
  const welch = useRef<{model: WelchModel; title: string; truncated: boolean} | null>(null);
  const request = useRef(0);
  const kpis = [...new Set(report.rows.map(row => row.kpi))];
  // The first report of a running experiment may list no KPI yet: default to the first one listed now.
  const [chosen, setKey] = useState('');
  const key = chosen || kpis[0] || '';
  const [configuration, setConfiguration] = useState(0);
  const [span, setSpan] = useState('5');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const overlay = new OverlayChart(host.current!); chart.current = overlay;
    return () => {request.current++; overlay.dispose(); chart.current = null;};
  }, []);
  const names = new Map(report.replications.flatMap(item => item.attempt === null ? []
    : [[item.attempt, `${report.configurations[item.configuration]?.label ?? item.configuration} #${item.index}`] as const]));
  const fetchSeries = async (attempts: string[]): Promise<SeriesEntry[]> => {
    const listed: SeriesEntry[] = [];
    for (let offset = 0; offset < attempts.length; offset += BATCH) {
      const result = await client.command('series.get', {attempts: attempts.slice(offset, offset + BATCH), keys: [key]});
      if (!result.ok) throw new Error(JSON.stringify(result.error));
      listed.push(...result.series as SeriesEntry[]);
    }
    return listed;
  };
  const run = async (work: (id: number) => Promise<void>) => {
    const id = ++request.current; setBusy(true); setNote('');
    try {await work(id);} catch (error) {if (id === request.current) setNote(error instanceof Error ? error.message : String(error));}
    finally {if (id === request.current) setBusy(false);}
  };
  const overlay = () => void run(async id => {
    welch.current = null;
    const attempts = selected.filter(committed).map(item => item.attempt!);
    if (!attempts.length || attempts.length > MAX_OVERLAY) throw new Error(`Select 1 to ${MAX_OVERLAY} committed replications to overlay`);
    const inputs = seriesInputs(await fetchSeries(attempts), [key], attempt => names.get(attempt) ?? attempt.slice(0, 8));
    if (id !== request.current) return;
    chart.current?.render(inputs, `${key} · ${inputs.length} recordings`);
    if (inputs.some(input => input.truncated)) setNote('Some series are truncated to the first 100 000 points.');
  });
  const draw = (raw: string) => {
    const current = welch.current, size = Number(raw);
    if (!current || !/^[1-9][0-9]*$/.test(raw) || size % 2 === 0) return;
    const points = current.model.points(size);
    chart.current?.render([{label: `${current.title} · window ${size}${current.truncated ? ' (truncated input)' : ''}`, points, truncated: current.truncated}],
      `Welch moving average · ${key}`, {stepped: false});
  };
  const loadWelch = () => void run(async id => {
    // List this configuration's replications on their own, so the report's cap cannot hide them.
    const result = await client.command('comparison.get', {run_id: report.run_id, configuration});
    if (!result.ok) throw new Error(JSON.stringify(result.error));
    const {attempts, message} = welchAttempts(result.report as ExperimentReport, configuration);
    if (message) throw new Error(message);
    const used = attempts.slice(0, WELCH_REPLICATIONS);
    const entries = await fetchSeries(used);
    if (id !== request.current) return;
    const series = entries.map(entry => finitePoints(entry.series[key]?.points ?? []));
    const truncated = entries.some(entry => entry.series[key]?.truncated);
    welch.current = {model: new WelchModel(series, uniformGrid(series, WELCH_GRID)),
      title: `${report.configurations[configuration]?.label ?? configuration} · ${used.length} replications`, truncated};
    draw(span);
    if (used.length < attempts.length) setNote(`Using the first ${used.length} of ${attempts.length} committed replications.`);
  });
  const windowValid = /^[1-9][0-9]*$/.test(span) && Number(span) % 2 === 1;
  return <div className="experiment-charts">
    <div className="row">
      <label>KPI <input list="experiment-kpis" value={key} onChange={event => {setKey(event.target.value); welch.current = null;}} /></label>
      <datalist id="experiment-kpis">{kpis.map(item => <option key={item} value={item} />)}</datalist>
      <button disabled={busy || !key || !selected.length} onClick={overlay}>Overlay selected ({selected.length}/{MAX_OVERLAY})</button>
      <label>Configuration <select value={configuration} onChange={event => {setConfiguration(Number(event.target.value)); welch.current = null;}}>
        {report.configurations.map(item => <option key={item.index} value={item.index}>{item.label}</option>)}
      </select></label>
      <label>Window <input value={span} inputMode="numeric" aria-invalid={!windowValid} onChange={event => {
        setSpan(event.target.value);
        try {draw(event.target.value);} catch (error) {setNote(error instanceof Error ? error.message : String(error));}
      }} /></label>
      <button disabled={busy || !key || !windowValid} onClick={loadWelch}>Welch plot</button>
      {!windowValid && <small role="alert">The window is an odd number of grid points</small>}
    </div>
    <p className="muted">Welch: replications averaged on a {WELCH_GRID}-point grid with previous values, then a centred moving average. Warm-up stays your choice.</p>
    {note && <p role="status">{note}</p>}
    <div className="chart" ref={host} />
  </div>;
}
