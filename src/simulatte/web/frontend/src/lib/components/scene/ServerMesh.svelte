<script lang="ts">
	import { T } from '@threlte/core';
	import type { Vector3Tuple } from 'three';

	interface Props {
		position: Vector3Tuple;
		serverId: number;
		queueLength?: number;
		utilization?: number;
		isProcessing?: boolean;
	}

	let {
		position,
		serverId,
		queueLength = 0,
		utilization = 0,
		isProcessing = false
	}: Props = $props();

	// Server color based on state
	let color = $derived(
		isProcessing ? 0x00cc88 : // Green when processing
		queueLength > 0 ? 0xcccc33 : // Yellow when jobs waiting
		0x666666 // Gray when idle
	);
</script>

<T.Group position={position}>
	<!-- Server base (cube) -->
	<T.Mesh>
		<T.BoxGeometry args={[1, 0.5, 1]} />
		<T.MeshStandardMaterial {color} />
	</T.Mesh>

	<!-- Server top indicator -->
	<T.Mesh position={[0, 0.35, 0]}>
		<T.BoxGeometry args={[0.8, 0.2, 0.8]} />
		<T.MeshStandardMaterial
			color={isProcessing ? 0x00ff88 : 0x444444}
			emissive={isProcessing ? 0x00ff88 : 0x000000}
			emissiveIntensity={isProcessing ? 0.3 : 0}
		/>
	</T.Mesh>
</T.Group>
