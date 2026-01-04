<script lang="ts">
	import { T } from '@threlte/core';
	import type { Vector3Tuple } from 'three';

	interface Props {
		position: Vector3Tuple;
		color: number;
		scale?: number;
		isProcessing?: boolean;
	}

	let {
		position,
		color,
		scale = 0.3,
		isProcessing = false
	}: Props = $props();
</script>

<T.Mesh {position} scale={[scale, scale, scale]}>
	<T.BoxGeometry args={[1, 1, 1]} />
	<T.MeshStandardMaterial
		{color}
		emissive={isProcessing ? color : 0x000000}
		emissiveIntensity={isProcessing ? 0.2 : 0}
	/>
</T.Mesh>
