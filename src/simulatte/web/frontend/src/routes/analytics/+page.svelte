<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import type { AnalyticsSummary, TimeSeriesData } from '$lib/types/simulation';
	import WIPChart from '$lib/components/charts/WIPChart.svelte';

	let summary = $state<AnalyticsSummary | null>(null);
	let wipData = $state<TimeSeriesData | null>(null);
	let throughputData = $state<TimeSeriesData | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);

	async function loadAnalytics() {
		loading = true;
		error = null;
		try {
			summary = await api.getAnalyticsSummary();
			wipData = await api.getTimeSeries('wip');
			throughputData = await api.getTimeSeries('jobs_completed');
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadAnalytics();
	});
</script>

<div class="analytics-page">
	<div class="page-header">
		<h2>Analytics</h2>
		<button onclick={loadAnalytics} disabled={loading}>
			{loading ? 'Loading...' : 'Refresh'}
		</button>
	</div>

	{#if error}
		<div class="error-card card">
			<p>{error}</p>
			<p class="hint">Run a simulation first to see analytics.</p>
		</div>
	{:else if loading}
		<div class="loading">Loading analytics...</div>
	{:else if summary}
		<div class="metrics-grid">
			<div class="metric-card card">
				<div class="metric-value">{summary.completed_jobs}</div>
				<div class="metric-label">Jobs Completed</div>
			</div>
			<div class="metric-card card">
				<div class="metric-value">{summary.avg_makespan.toFixed(1)}</div>
				<div class="metric-label">Avg Makespan</div>
			</div>
			<div class="metric-card card">
				<div class="metric-value">{(summary.on_time_rate * 100).toFixed(1)}%</div>
				<div class="metric-label">On-Time Rate</div>
			</div>
			<div class="metric-card card">
				<div class="metric-value">{(summary.tardy_rate * 100).toFixed(1)}%</div>
				<div class="metric-label">Tardy Rate</div>
			</div>
			<div class="metric-card card">
				<div class="metric-value">{summary.max_wip.toFixed(1)}</div>
				<div class="metric-label">Max WIP</div>
			</div>
			<div class="metric-card card">
				<div class="metric-value">{summary.avg_queue_time.toFixed(1)}</div>
				<div class="metric-label">Avg Queue Time</div>
			</div>
		</div>

		<div class="charts-grid">
			{#if wipData}
				<div class="chart-card card">
					<h3>Work in Progress Over Time</h3>
					<WIPChart data={wipData.data} />
				</div>
			{/if}

			{#if throughputData}
				<div class="chart-card card">
					<h3>Cumulative Throughput</h3>
					<WIPChart data={throughputData.data} color="#00cc88" />
				</div>
			{/if}
		</div>

		<div class="utilization-section card">
			<h3>Server Utilization</h3>
			<div class="utilization-bars">
				{#each Object.entries(summary.server_utilizations) as [serverId, util]}
					<div class="util-bar-container">
						<div class="util-label">Server {serverId}</div>
						<div class="util-bar-bg">
							<div
								class="util-bar-fill"
								style="width: {util * 100}%"
							></div>
						</div>
						<div class="util-value">{(util * 100).toFixed(1)}%</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>

<style>
	.analytics-page {
		padding: var(--spacing-lg);
		overflow-y: auto;
		height: 100%;
	}

	.page-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: var(--spacing-lg);
	}

	.page-header h2 {
		margin: 0;
	}

	.error-card {
		text-align: center;
		padding: var(--spacing-xl);
	}

	.hint {
		color: var(--text-muted);
		margin-top: var(--spacing-sm);
	}

	.loading {
		text-align: center;
		padding: var(--spacing-xl);
		color: var(--text-secondary);
	}

	.metrics-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: var(--spacing-md);
		margin-bottom: var(--spacing-lg);
	}

	.metric-card {
		text-align: center;
		padding: var(--spacing-lg);
	}

	.metric-value {
		font-size: 2rem;
		font-weight: 700;
		color: var(--accent-primary);
		line-height: 1;
	}

	.metric-label {
		font-size: 0.85rem;
		color: var(--text-secondary);
		margin-top: var(--spacing-sm);
	}

	.charts-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
		gap: var(--spacing-md);
		margin-bottom: var(--spacing-lg);
	}

	.chart-card {
		padding: var(--spacing-md);
	}

	.chart-card h3 {
		margin-bottom: var(--spacing-md);
		font-size: 1rem;
		color: var(--text-secondary);
	}

	.utilization-section {
		padding: var(--spacing-md);
	}

	.utilization-section h3 {
		margin-bottom: var(--spacing-md);
		font-size: 1rem;
		color: var(--text-secondary);
	}

	.utilization-bars {
		display: flex;
		flex-direction: column;
		gap: var(--spacing-sm);
	}

	.util-bar-container {
		display: flex;
		align-items: center;
		gap: var(--spacing-md);
	}

	.util-label {
		width: 80px;
		font-size: 0.9rem;
		color: var(--text-secondary);
	}

	.util-bar-bg {
		flex: 1;
		height: 20px;
		background: var(--bg-tertiary);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}

	.util-bar-fill {
		height: 100%;
		background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
		transition: width 0.3s ease;
	}

	.util-value {
		width: 60px;
		text-align: right;
		font-size: 0.9rem;
		font-weight: 500;
	}
</style>
