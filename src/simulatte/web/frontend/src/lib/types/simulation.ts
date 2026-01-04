export interface SimulationStatus {
	run_id: number | null;
	state: 'idle' | 'running' | 'completed' | 'error';
	progress: number;
	current_time: number;
	until_time: number | null;
	error_message: string | null;
}

export interface ServerState {
	id: number;
	queue_length: number;
	processing_job: string | null;
	utilization: number;
	wip: number;
}

export interface JobState {
	id: string;
	sku: string;
	location: 'psp' | 'queue' | 'processing' | 'transit' | 'completed';
	server_id: number | null;
	queue_position: number | null;
	urgency: number;
	due_date: number;
	created_at: number;
	color: string;
}

export interface Snapshot {
	id: number;
	sim_time: number;
	servers: ServerState[];
	jobs: JobState[];
	psp_jobs: string[];
	wip_total: number;
	wip_per_server: Record<string, number>;
	jobs_completed: number;
}

export interface SnapshotListItem {
	id: number;
	sim_time: number;
	job_count: number;
	wip_total: number;
}

export interface AnalyticsSummary {
	run_id: number;
	total_jobs: number;
	completed_jobs: number;
	avg_makespan: number;
	avg_tardiness: number;
	on_time_rate: number;
	tardy_rate: number;
	early_rate: number;
	avg_wip: number;
	max_wip: number;
	server_utilizations: Record<string, number>;
	avg_queue_time: number;
}

export interface TimeSeriesPoint {
	time: number;
	value: number;
}

export interface TimeSeriesData {
	run_id: number;
	metric: string;
	data: TimeSeriesPoint[];
}
