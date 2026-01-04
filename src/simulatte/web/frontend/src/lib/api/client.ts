import type {
	SimulationStatus,
	Snapshot,
	SnapshotListItem,
	AnalyticsSummary,
	TimeSeriesData
} from '$lib/types/simulation';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
	const res = await fetch(`${API_BASE}${url}`, {
		...options,
		headers: {
			'Content-Type': 'application/json',
			...options?.headers
		}
	});

	if (!res.ok) {
		const error = await res.json().catch(() => ({ error: res.statusText }));
		throw new Error(error.detail || error.error || 'Request failed');
	}

	return res.json();
}

export const api = {
	// Simulation control
	getStatus: () => fetchJson<SimulationStatus>('/simulation/status'),

	startSimulation: (until: number, seed?: number, snapshotInterval = 10) =>
		fetchJson<SimulationStatus>('/simulation/run', {
			method: 'POST',
			body: JSON.stringify({
				until,
				seed,
				snapshot_interval: snapshotInterval
			})
		}),

	startMultipleSimulations: (until: number, seeds: number[], snapshotInterval = 10) =>
		fetchJson<SimulationStatus>('/simulation/run-multiple', {
			method: 'POST',
			body: JSON.stringify({
				until,
				seeds,
				snapshot_interval: snapshotInterval
			})
		}),

	stopSimulation: () =>
		fetchJson<{ success: boolean }>('/simulation/stop', { method: 'POST' }),

	// Snapshots
	listSnapshots: (runId?: number, limit = 1000, offset = 0) => {
		const params = new URLSearchParams();
		if (runId !== undefined) params.append('run_id', String(runId));
		params.append('limit', String(limit));
		params.append('offset', String(offset));
		return fetchJson<{
			run_id: number;
			total: number;
			snapshots: SnapshotListItem[];
		}>(`/snapshots?${params}`);
	},

	getSnapshot: (snapshotId: number) =>
		fetchJson<Snapshot>(`/snapshots/${snapshotId}`),

	// Analytics
	getAnalyticsSummary: (runId?: number) => {
		const params = runId !== undefined ? `?run_id=${runId}` : '';
		return fetchJson<AnalyticsSummary>(`/analytics/summary${params}`);
	},

	getTimeSeries: (metric: string, runId?: number) => {
		const params = runId !== undefined ? `?run_id=${runId}` : '';
		return fetchJson<TimeSeriesData>(`/analytics/timeseries/${metric}${params}`);
	}
};
