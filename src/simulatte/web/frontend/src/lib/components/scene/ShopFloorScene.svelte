<script lang="ts">
	import { T, Canvas } from '@threlte/core';
	import { OrbitControls, Grid } from '@threlte/extras';
	import ServerMesh from './ServerMesh.svelte';
	import JobCube from './JobCube.svelte';
	import PSPArea from './PSPArea.svelte';
	import { circularLayout, pspPosition, queueLayout, pspJobLayout } from '$lib/utils/layout';
	import { hslToHex, blendWithUrgency } from '$lib/utils/colors';
	import type { Snapshot, JobState } from '$lib/types/simulation';

	interface Props {
		snapshot: Snapshot | null;
	}

	let { snapshot }: Props = $props();

	// Calculate server positions (circular layout)
	let serverPositions = $derived(
		snapshot ? circularLayout(snapshot.servers.length, 4) : []
	);

	// PSP position
	let pspPos = $derived(pspPosition(4));

	// Get jobs by location
	let jobsInPsp = $derived(
		snapshot?.jobs.filter(j => j.location === 'psp') ?? []
	);

	let jobsByServer = $derived.by(() => {
		if (!snapshot) return new Map<number, JobState[]>();
		const map = new Map<number, JobState[]>();
		for (const job of snapshot.jobs) {
			if (job.location === 'queue' && job.server_id !== null) {
				if (!map.has(job.server_id)) map.set(job.server_id, []);
				map.get(job.server_id)!.push(job);
			}
		}
		return map;
	});

	let processingJobs = $derived(
		snapshot?.jobs.filter(j => j.location === 'processing') ?? []
	);
</script>

<Canvas>
	<!-- Camera -->
	<T.OrthographicCamera
		makeDefault
		position={[10, 10, 10]}
		zoom={50}
		near={0.1}
		far={1000}
	>
		<OrbitControls
			enableRotate={false}
			enablePan={true}
			enableZoom={true}
			minZoom={20}
			maxZoom={200}
		/>
	</T.OrthographicCamera>

	<!-- Lighting -->
	<T.AmbientLight intensity={0.6} />
	<T.DirectionalLight position={[5, 10, 5]} intensity={0.8} />

	<!-- Ground grid -->
	<Grid
		cellColor="#444"
		sectionColor="#666"
		fadeDistance={30}
		cellSize={1}
		sectionSize={5}
		infiniteGrid={true}
	/>

	<!-- Servers -->
	{#each serverPositions as pos, i}
		{@const server = snapshot?.servers[i]}
		{@const queuedJobs = jobsByServer.get(i) ?? []}
		{@const processingJob = processingJobs.find(j => j.server_id === i)}

		<ServerMesh
			position={pos}
			serverId={i}
			queueLength={server?.queue_length ?? 0}
			utilization={server?.utilization ?? 0}
			isProcessing={!!processingJob}
		/>

		<!-- Queued jobs behind server -->
		{#each queueLayout(pos, queuedJobs.length) as queuePos, qi}
			{@const job = queuedJobs[qi]}
			<JobCube
				position={queuePos}
				color={blendWithUrgency(hslToHex(job.color), job.urgency)}
				scale={0.3}
			/>
		{/each}

		<!-- Processing job on server -->
		{#if processingJob}
			<JobCube
				position={[pos[0], pos[1] + 0.7, pos[2]]}
				color={blendWithUrgency(hslToHex(processingJob.color), processingJob.urgency)}
				scale={0.4}
				isProcessing={true}
			/>
		{/if}
	{/each}

	<!-- PSP Area -->
	<PSPArea position={pspPos} jobCount={jobsInPsp.length} />

	<!-- Jobs in PSP -->
	{#each pspJobLayout(pspPos, jobsInPsp.length) as jobPos, i}
		{@const job = jobsInPsp[i]}
		<JobCube
			position={jobPos}
			color={blendWithUrgency(hslToHex(job.color), job.urgency)}
			scale={0.25}
		/>
	{/each}
</Canvas>
