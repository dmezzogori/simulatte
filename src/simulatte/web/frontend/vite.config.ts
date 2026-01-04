import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': {
				target: 'http://localhost:8765',
				changeOrigin: true
			}
		}
	},
	ssr: {
		noExternal: ['three', '@threlte/core', '@threlte/extras']
	}
});
