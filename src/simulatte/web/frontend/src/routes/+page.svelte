<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { simulation, isRunning, isCompleted } from '$lib/stores/simulation';
	import { playback } from '$lib/stores/playback';
	import { api } from '$lib/api/client';
	import ShopFloorScene from '$lib/components/scene/ShopFloorScene.svelte';
	import PlaybackControls from '$lib/components/controls/PlaybackControls.svelte';
	import ConsolePanel from '$lib/components/overlay/ConsolePanel.svelte';

	let until = $state(100);
	let snapshotInterval = $state(5);
	let consoleMessages = $state<string[]>([]);
	let lastLoadedSnapshotIndex = $state(-1);
	let snapshotsLoadedForRun = $state<number | null>(null);

	function log(message: string) {
		consoleMessages = [...consoleMessages.slice(-99), `[${new Date().toLocaleTimeString()}] ${message}`];
	}

	async function startSimulation() {
		try {
			log('Starting simulation...');
			simulation.reset();
			playback.reset();
			lastLoadedSnapshotIndex = -1;
			snapshotsLoadedForRun = null;

			await api.startSimulation(until, undefined, snapshotInterval);
			simulation.startPolling();
			log('Simulation started');
		} catch (e: any) {
			log(`Error: ${e.message}`);
		}
	}

	async function stopSimulation() {
		try {
			await api.stopSimulation();
			simulation.stopPolling();
			log('Simulation stopped');
		} catch (e: any) {
			log(`Error: ${e.message}`);
		}
	}

	async function loadSnapshots() {
		try {
			const { snapshots, total } = await api.listSnapshots();
			simulation.setSnapshots(snapshots);
			playback.setMaxIndex(snapshots.length - 1);
			log(`Loaded ${total} snapshots`);

			if (snapshots.length > 0) {
				const firstSnapshot = await api.getSnapshot(snapshots[0].id);
				simulation.setCurrentSnapshot(firstSnapshot, 0);
			}
		} catch (e: any) {
			log(`Error loading snapshots: ${e.message}`);
		}
	}

	// Watch for completion and auto-load snapshots
	$effect(() => {
		const runId = $simulation.runId;
		if ($isCompleted && runId !== null && snapshotsLoadedForRun !== runId) {
			snapshotsLoadedForRun = runId;
			log('Simulation completed, loading snapshots...');
			loadSnapshots();
		}
	});

	// Watch playback index changes and load corresponding snapshot
	let currentPlaybackIndex = $derived($playback.currentIndex);
	let snapshots = $derived($simulation.snapshots);

	$effect(() => {
		if (snapshots.length > 0 &&
			currentPlaybackIndex < snapshots.length &&
			currentPlaybackIndex !== lastLoadedSnapshotIndex) {

			const indexToLoad = currentPlaybackIndex;
			lastLoadedSnapshotIndex = indexToLoad;

			const snapshotItem = snapshots[indexToLoad];
			api.getSnapshot(snapshotItem.id).then(snapshot => {
				simulation.setCurrentSnapshot(snapshot, indexToLoad);
			}).catch(e => {
				lastLoadedSnapshotIndex = -1;
				log(`Error loading snapshot: ${e.message}`);
			});
		}
	});

	onMount(() => {
		log('Simulatte Web UI ready');
	});

	onDestroy(() => {
		simulation.stopPolling();
		playback.pause();
	});
</script>

<div class="visualization-page">
	<div class="sidebar">
		<div class="card">
			<h3>Simulation Control</h3>
			<div class="control-group">
				<label>
					Run until (time)
					<input type="number" bind:value={until} min="1" step="10" />
				</label>
				<label>
					Snapshot interval
					<input type="number" bind:value={snapshotInterval} min="1" step="1" />
				</label>
			</div>

			<div class="button-group">
				{#if $isRunning}
					<button class="secondary" onclick={stopSimulation}>Stop</button>
				{:else}
					<button onclick={startSimulation}>Run Simulation</button>
				{/if}
			</div>

			{#if $isRunning}
				<div class="progress-bar">
					<div class="progress-fill" style="width: {$simulation.status.progress * 100}%"></div>
				</div>
				<div class="progress-text">
					{($simulation.status.progress * 100).toFixed(1)}% - t={$simulation.status.current_time.toFixed(1)}
				</div>
			{/if}

			{#if $simulation.status.error_message}
				<div class="error-message">{$simulation.status.error_message}</div>
			{/if}
		</div>

		<div class="card">
			<h3>Status</h3>
			<div class="status-info">
				<div class="status-row">
					<span>State:</span>
					<span class="status-{$simulation.status.state}">{$simulation.status.state}</span>
				</div>
				<div class="status-row">
					<span>Snapshots:</span>
					<span>{$simulation.snapshots.length}</span>
				</div>
				{#if $simulation.currentSnapshot}
					<div class="status-row">
						<span>Sim Time:</span>
						<span>{$simulation.currentSnapshot.sim_time.toFixed(1)}</span>
					</div>
					<div class="status-row">
						<span>Active Jobs:</span>
						<span>{$simulation.currentSnapshot.jobs.length}</span>
					</div>
					<div class="status-row">
						<span>Total WIP:</span>
						<span>{$simulation.currentSnapshot.wip_total.toFixed(1)}</span>
					</div>
				{/if}
			</div>
		</div>
	</div>

	<div class="main-view">
		<div class="scene-container">
			<ShopFloorScene snapshot={$simulation.currentSnapshot} />
		</div>

		<PlaybackControls
			disabled={$simulation.snapshots.length === 0}
			snapshotCount={$simulation.snapshots.length}
		/>

		<ConsolePanel messages={consoleMessages} />
	</div>
</div>

<style>
	.visualization-page {
		display: flex;
		flex: 1;
		overflow: hidden;
	}

	.sidebar {
		width: 280px;
		padding: var(--spacing-md);
		display: flex;
		flex-direction: column;
		gap: var(--spacing-md);
		overflow-y: auto;
		border-right: 1px solid var(--border-color);
		background-color: var(--bg-secondary);
	}

	.sidebar h3 {
		margin-bottom: var(--spacing-sm);
		font-size: 0.9rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-secondary);
	}

	.control-group {
		display: flex;
		flex-direction: column;
		gap: var(--spacing-sm);
	}

	.control-group label {
		display: flex;
		flex-direction: column;
		gap: var(--spacing-xs);
		font-size: 0.9rem;
		color: var(--text-secondary);
	}

	.control-group input {
		padding: var(--spacing-sm);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-sm);
		background: var(--bg-primary);
		color: var(--text-primary);
	}

	.button-group {
		display: flex;
		gap: var(--spacing-sm);
		margin-top: var(--spacing-sm);
	}

	.button-group button {
		flex: 1;
	}

	.progress-bar {
		height: 4px;
		background: var(--bg-tertiary);
		border-radius: 2px;
		margin-top: var(--spacing-sm);
		overflow: hidden;
	}

	.progress-fill {
		height: 100%;
		background: var(--accent-primary);
		transition: width 0.2s ease;
	}

	.progress-text {
		font-size: 0.8rem;
		color: var(--text-secondary);
		margin-top: var(--spacing-xs);
		text-align: center;
	}

	.error-message {
		color: var(--error);
		font-size: 0.85rem;
		margin-top: var(--spacing-sm);
		padding: var(--spacing-sm);
		background: rgba(204, 51, 51, 0.1);
		border-radius: var(--radius-sm);
	}

	.status-info {
		display: flex;
		flex-direction: column;
		gap: var(--spacing-xs);
	}

	.status-row {
		display: flex;
		justify-content: space-between;
		font-size: 0.9rem;
	}

	.status-row span:first-child {
		color: var(--text-secondary);
	}

	.main-view {
		flex: 1;
		display: flex;
		flex-direction: column;
		overflow: hidden;
	}

	.scene-container {
		flex: 1;
		position: relative;
		background: var(--scene-bg);
	}
</style>
