import { writable, derived } from 'svelte/store';

interface PlaybackState {
	playing: boolean;
	speed: number;
	currentIndex: number;
	maxIndex: number;
}

function createPlaybackStore() {
	const { subscribe, set, update } = writable<PlaybackState>({
		playing: false,
		speed: 1,
		currentIndex: 0,
		maxIndex: 0
	});

	let animationFrame: number | null = null;
	let lastTime = 0;

	const tick = (timestamp: number) => {
		update(state => {
			if (!state.playing) return state;

			const delta = timestamp - lastTime;
			lastTime = timestamp;

			// Advance based on speed (ms per snapshot)
			const msPerSnapshot = 500 / state.speed;
			if (delta >= msPerSnapshot) {
				const newIndex = Math.min(state.currentIndex + 1, state.maxIndex);
				if (newIndex >= state.maxIndex) {
					// Reached end, stop playing
					return { ...state, playing: false, currentIndex: newIndex };
				}
				return { ...state, currentIndex: newIndex };
			}

			return state;
		});

		let currentState: PlaybackState;
		const unsubscribe = subscribe(s => currentState = s);
		unsubscribe();

		if (currentState!.playing) {
			animationFrame = requestAnimationFrame(tick);
		}
	};

	return {
		subscribe,

		play: () => {
			update(s => {
				if (s.currentIndex >= s.maxIndex) {
					// At end, restart from beginning
					return { ...s, playing: true, currentIndex: 0 };
				}
				return { ...s, playing: true };
			});
			lastTime = performance.now();
			animationFrame = requestAnimationFrame(tick);
		},

		pause: () => {
			update(s => ({ ...s, playing: false }));
			if (animationFrame) {
				cancelAnimationFrame(animationFrame);
				animationFrame = null;
			}
		},

		toggle: () => {
			let currentState: PlaybackState;
			const unsubscribe = subscribe(s => currentState = s);
			unsubscribe();

			if (currentState!.playing) {
				update(s => ({ ...s, playing: false }));
				if (animationFrame) {
					cancelAnimationFrame(animationFrame);
					animationFrame = null;
				}
			} else {
				update(s => {
					if (s.currentIndex >= s.maxIndex) {
						return { ...s, playing: true, currentIndex: 0 };
					}
					return { ...s, playing: true };
				});
				lastTime = performance.now();
				animationFrame = requestAnimationFrame(tick);
			}
		},

		setSpeed: (speed: number) => {
			update(s => ({ ...s, speed }));
		},

		seek: (index: number) => {
			update(s => ({
				...s,
				currentIndex: Math.max(0, Math.min(index, s.maxIndex))
			}));
		},

		setMaxIndex: (maxIndex: number) => {
			update(s => ({
				...s,
				maxIndex,
				currentIndex: Math.min(s.currentIndex, maxIndex)
			}));
		},

		reset: () => {
			if (animationFrame) {
				cancelAnimationFrame(animationFrame);
				animationFrame = null;
			}
			set({
				playing: false,
				speed: 1,
				currentIndex: 0,
				maxIndex: 0
			});
		}
	};
}

export const playback = createPlaybackStore();
