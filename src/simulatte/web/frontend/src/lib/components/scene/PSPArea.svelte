<script lang="ts">
	import { T } from '@threlte/core';
	import type { Vector3Tuple } from 'three';

	interface Props {
		position: Vector3Tuple;
		jobCount?: number;
	}

	let { position, jobCount = 0 }: Props = $props();
</script>

<T.Group {position}>
	<!-- PSP platform -->
	<T.Mesh rotation.x={-Math.PI / 2}>
		<T.PlaneGeometry args={[3, 3]} />
		<T.MeshStandardMaterial
			color={0x333355}
			transparent={true}
			opacity={0.5}
		/>
	</T.Mesh>

	<!-- Border -->
	<T.Mesh rotation.x={-Math.PI / 2} position.y={0.01}>
		<T.RingGeometry args={[1.4, 1.5, 32]} />
		<T.MeshBasicMaterial color={0x6666aa} />
	</T.Mesh>
</T.Group>
