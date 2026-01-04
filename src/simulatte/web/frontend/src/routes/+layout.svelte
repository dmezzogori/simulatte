<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { preferences } from '$lib/stores/preferences';

	let { children } = $props();

	onMount(() => {
		// Apply theme based on preferences
		const updateTheme = () => {
			const theme = $preferences.theme;
			if (theme === 'system') {
				const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
				document.documentElement.dataset.theme = systemDark ? 'dark' : 'light';
			} else {
				document.documentElement.dataset.theme = theme;
			}
		};

		updateTheme();

		// Listen for system theme changes
		const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
		mediaQuery.addEventListener('change', updateTheme);

		return () => {
			mediaQuery.removeEventListener('change', updateTheme);
		};
	});
</script>

<div class="app">
	<header class="header">
		<div class="logo">
			<span class="logo-text">Simulatte</span>
		</div>
		<nav class="nav">
			<a href="/" class="nav-link">Visualization</a>
			<a href="/analytics" class="nav-link">Analytics</a>
		</nav>
		<div class="header-actions">
			<button
				class="theme-toggle secondary"
				onclick={() => {
					const themes = ['light', 'dark', 'system'] as const;
					const currentIndex = themes.indexOf($preferences.theme);
					const nextIndex = (currentIndex + 1) % themes.length;
					preferences.setTheme(themes[nextIndex]);
				}}
			>
				{$preferences.theme === 'light' ? '☀️' : $preferences.theme === 'dark' ? '🌙' : '⚙️'}
			</button>
		</div>
	</header>

	<main class="main">
		{@render children()}
	</main>
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		height: 100vh;
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--spacing-sm) var(--spacing-lg);
		background-color: var(--bg-secondary);
		border-bottom: 1px solid var(--border-color);
		height: 56px;
	}

	.logo {
		display: flex;
		align-items: center;
		gap: var(--spacing-sm);
	}

	.logo-text {
		font-size: 1.25rem;
		font-weight: 700;
		background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
		-webkit-background-clip: text;
		-webkit-text-fill-color: transparent;
		background-clip: text;
	}

	.nav {
		display: flex;
		gap: var(--spacing-lg);
	}

	.nav-link {
		color: var(--text-secondary);
		font-weight: 500;
		transition: color var(--transition-fast);
	}

	.nav-link:hover {
		color: var(--text-primary);
		text-decoration: none;
	}

	.header-actions {
		display: flex;
		gap: var(--spacing-sm);
	}

	.theme-toggle {
		width: 40px;
		height: 40px;
		padding: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 1.2rem;
	}

	.main {
		flex: 1;
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
</style>
