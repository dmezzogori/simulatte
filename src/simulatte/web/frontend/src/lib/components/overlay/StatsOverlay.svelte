<script lang="ts">
	// Placeholder for future stats overlay functionality
	// This will show hover tooltips for servers with queue length and utilization

	interface Props {
		visible?: boolean;
		x?: number;
		y?: number;
		serverId?: number;
		queueLength?: number;
		utilization?: number;
	}

	let {
		visible = false,
		x = 0,
		y = 0,
		serverId = 0,
		queueLength = 0,
		utilization = 0
	}: Props = $props();
</script>

{#if visible}
	<div class="stats-overlay" style="left: {x}px; top: {y}px;">
		<div class="overlay-title">Server {serverId}</div>
		<div class="overlay-stat">
			<span>Queue:</span>
			<span>{queueLength}</span>
		</div>
		<div class="overlay-stat">
			<span>Utilization:</span>
			<span>{(utilization * 100).toFixed(1)}%</span>
		</div>
	</div>
{/if}

<style>
	.stats-overlay {
		position: fixed;
		background: rgba(20, 20, 40, 0.95);
		border: 1px solid var(--border-color);
		border-radius: var(--radius-md);
		padding: var(--spacing-sm) var(--spacing-md);
		pointer-events: none;
		z-index: 1000;
		min-width: 120px;
	}

	.overlay-title {
		font-weight: 600;
		margin-bottom: var(--spacing-xs);
		color: var(--accent-primary);
	}

	.overlay-stat {
		display: flex;
		justify-content: space-between;
		font-size: 0.85rem;
		color: var(--text-secondary);
	}
</style>
