import { writable } from 'svelte/store';

export type Theme = 'light' | 'dark' | 'system';

interface Preferences {
	theme: Theme;
}

function createPreferencesStore() {
	const { subscribe, set, update } = writable<Preferences>({
		theme: 'system'
	});

	return {
		subscribe,
		setTheme: (theme: Theme) => {
			update(p => ({ ...p, theme }));
			// Persist to backend
			fetch('/api/preferences', {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ theme })
			}).catch(console.error);
		},
		load: async () => {
			try {
				const res = await fetch('/api/preferences');
				if (res.ok) {
					const data = await res.json();
					set({ theme: data.theme || 'system' });
				}
			} catch (e) {
				console.error('Failed to load preferences:', e);
			}
		}
	};
}

export const preferences = createPreferencesStore();
