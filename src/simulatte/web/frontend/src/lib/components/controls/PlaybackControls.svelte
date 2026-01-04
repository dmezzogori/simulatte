<script lang="ts">
	import { playback } from '$lib/stores/playback';
	import { simulation } from '$lib/stores/simulation';

	interface Props {
		disabled?: boolean;
		snapshotCount?: number;
	}

	let { disabled = false, snapshotCount = 0 }: Props = $props();

	const speeds = [1, 2, 5, 10];

	function formatTime(index: number): string {
		const snapshots = $simulation.snapshots;
		if (index < snapshots.length) {
			return snapshots[index].sim_time.toFixed(1);
		}
		return '0.0';
	}
</script>

<div class="playback-controls" class:disabled>
	<div class="controls-row">
		<button
			class="play-btn"
			onclick={() => playback.toggle()}
			{disabled}
		>
			{$playback.playing ? '⏸' : '▶'}
		</button>

		<div class="speed-controls">
			{#each speeds as speed}
				<button
					class="speed-btn"
					class:active={$playback.speed === speed}
					onclick={() => playback.setSpeed(speed)}
					{disabled}
				>
					{speed}x
				</button>
			{/each}
		</div>

		<div class="time-display">
			t = {formatTime($playback.currentIndex)}
		</div>
	</div>

	<div class="timeline-row">
		<input
			type="range"
			min="0"
			max={snapshotCount - 1}
			value={$playback.currentIndex}
			oninput={(e) => playback.seek(parseInt((e.target as HTMLInputElement).value))}
			{disabled}
			class="timeline-slider"
		/>
		<span class="frame-count">
			{$playback.currentIndex + 1} / {snapshotCount}
		</span>
	</div>
</div>

<style>
	.playback-controls {
		padding: var(--spacing-sm) var(--spacing-md);
		background: var(--bg-secondary);
		border-top: 1px solid var(--border-color);
	}

	.playback-controls.disabled {
		opacity: 0.5;
		pointer-events: none;
	}

	.controls-row {
		display: flex;
		align-items: center;
		gap: var(--spacing-md);
		margin-bottom: var(--spacing-sm);
	}

	.play-btn {
		width: 40px;
		height: 40px;
		border-radius: 50%;
		font-size: 1.2rem;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0;
	}

	.speed-controls {
		display: flex;
		gap: var(--spacing-xs);
	}

	.speed-btn {
		padding: var(--spacing-xs) var(--spacing-sm);
		font-size: 0.8rem;
		background: var(--bg-tertiary);
		color: var(--text-secondary);
	}

	.speed-btn.active {
		background: var(--accent-primary);
		color: white;
	}

	.time-display {
		margin-left: auto;
		font-family: monospace;
		font-size: 0.9rem;
		color: var(--text-secondary);
	}

	.timeline-row {
		display: flex;
		align-items: center;
		gap: var(--spacing-md);
	}

	.timeline-slider {
		flex: 1;
		height: 4px;
		-webkit-appearance: none;
		appearance: none;
		background: var(--bg-tertiary);
		border-radius: 2px;
		cursor: pointer;
	}

	.timeline-slider::-webkit-slider-thumb {
		-webkit-appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: var(--accent-primary);
		cursor: pointer;
	}

	.frame-count {
		font-size: 0.8rem;
		color: var(--text-muted);
		min-width: 80px;
		text-align: right;
	}
</style>
