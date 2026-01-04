import { writable, derived } from 'svelte/store';
import type { SimulationStatus, Snapshot, SnapshotListItem } from '$lib/types/simulation';

interface SimulationState {
	status: SimulationStatus;
	snapshots: SnapshotListItem[];
	currentSnapshot: Snapshot | null;
	currentSnapshotIndex: number;
	runId: number | null;
}

const initialState: SimulationState = {
	status: {
		run_id: null,
		state: 'idle',
		progress: 0,
		current_time: 0,
		until_time: null,
		error_message: null
	},
	snapshots: [],
	currentSnapshot: null,
	currentSnapshotIndex: 0,
	runId: null
};

function createSimulationStore() {
	const { subscribe, set, update } = writable<SimulationState>(initialState);

	let pollInterval: ReturnType<typeof setInterval> | null = null;

	return {
		subscribe,

		reset: () => set(initialState),

		setStatus: (status: SimulationStatus) => {
			update(s => ({ ...s, status, runId: status.run_id }));
		},

		setSnapshots: (snapshots: SnapshotListItem[]) => {
			update(s => ({ ...s, snapshots }));
		},

		setCurrentSnapshot: (snapshot: Snapshot | null, index: number = 0) => {
			update(s => ({ ...s, currentSnapshot: snapshot, currentSnapshotIndex: index }));
		},

		startPolling: () => {
			if (pollInterval) return;
			pollInterval = setInterval(async () => {
				try {
					const res = await fetch('/api/simulation/status');
					if (res.ok) {
						const status = await res.json();
						update(s => ({ ...s, status, runId: status.run_id }));

						// Stop polling if completed or error
						if (status.state === 'completed' || status.state === 'error') {
							if (pollInterval) {
								clearInterval(pollInterval);
								pollInterval = null;
							}
						}
					}
				} catch (e) {
					console.error('Failed to poll status:', e);
				}
			}, 500);
		},

		stopPolling: () => {
			if (pollInterval) {
				clearInterval(pollInterval);
				pollInterval = null;
			}
		}
	};
}

export const simulation = createSimulationStore();

// Derived stores for convenience
export const isRunning = derived(simulation, $s => $s.status.state === 'running');
export const isCompleted = derived(simulation, $s => $s.status.state === 'completed');
export const hasError = derived(simulation, $s => $s.status.state === 'error');
