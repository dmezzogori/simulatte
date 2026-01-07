<script lang="ts">
	import { onMount, tick } from 'svelte';

	interface Props {
		messages?: string[];
		maxHeight?: number;
	}

	let { messages = [], maxHeight = 120 }: Props = $props();
	let consoleEl: HTMLDivElement;
	let collapsed = $state(false);

	// Auto-scroll to bottom when new messages arrive
	$effect(() => {
		if (messages.length && consoleEl) {
			tick().then(() => {
				consoleEl.scrollTop = consoleEl.scrollHeight;
			});
		}
	});
</script>

<div class="console-panel" class:collapsed>
	<button
		type="button"
		class="console-header"
		onclick={() => collapsed = !collapsed}
		aria-expanded={!collapsed}
	>
		<span class="console-title">Console</span>
		<span class="console-toggle">{collapsed ? '▲' : '▼'}</span>
	</button>

	{#if !collapsed}
		<div
			class="console-content"
			bind:this={consoleEl}
			style="max-height: {maxHeight}px"
		>
			{#each messages as message}
				<div class="console-line">{message}</div>
			{/each}
			{#if messages.length === 0}
				<div class="console-empty">No messages</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.console-panel {
		background: var(--bg-tertiary);
		border-top: 1px solid var(--border-color);
		font-family: 'Monaco', 'Menlo', monospace;
		font-size: 0.8rem;
	}

	.console-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
		padding: var(--spacing-xs) var(--spacing-md);
		cursor: pointer;
		user-select: none;
		background: var(--bg-secondary);
		border: none;
		font: inherit;
	}

	.console-header:hover {
		background: var(--bg-tertiary);
	}

	.console-title {
		font-weight: 500;
		color: var(--text-secondary);
		text-transform: uppercase;
		font-size: 0.7rem;
		letter-spacing: 0.05em;
	}

	.console-toggle {
		color: var(--text-muted);
		font-size: 0.7rem;
	}

	.console-content {
		overflow-y: auto;
		padding: var(--spacing-sm) var(--spacing-md);
	}

	.console-line {
		color: var(--text-secondary);
		line-height: 1.6;
		white-space: pre-wrap;
		word-break: break-word;
	}

	.console-line:hover {
		background: rgba(255, 255, 255, 0.05);
	}

	.console-empty {
		color: var(--text-muted);
		font-style: italic;
	}

	.console-panel.collapsed .console-content {
		display: none;
	}
</style>
