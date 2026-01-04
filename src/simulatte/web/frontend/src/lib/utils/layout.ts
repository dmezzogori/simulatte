import type { Vector3Tuple } from 'three';

/**
 * Calculate circular positions for servers.
 * @param serverCount Number of servers
 * @param radius Radius of the circle
 * @param centerY Y position (height)
 * @returns Array of [x, y, z] positions
 */
export function circularLayout(
	serverCount: number,
	radius: number = 5,
	centerY: number = 0
): Vector3Tuple[] {
	const positions: Vector3Tuple[] = [];

	for (let i = 0; i < serverCount; i++) {
		const angle = (i / serverCount) * Math.PI * 2 - Math.PI / 2;
		const x = Math.cos(angle) * radius;
		const z = Math.sin(angle) * radius;
		positions.push([x, centerY, z]);
	}

	return positions;
}

/**
 * Calculate position for PSP area (left of the server circle).
 */
export function pspPosition(radius: number = 5): Vector3Tuple {
	return [-radius - 3, 0, 0];
}

/**
 * Calculate queue positions behind a server.
 * @param serverPosition Server position
 * @param queueLength Number of jobs in queue
 * @param spacing Spacing between jobs
 * @returns Array of positions for queued jobs
 */
export function queueLayout(
	serverPosition: Vector3Tuple,
	queueLength: number,
	spacing: number = 0.6
): Vector3Tuple[] {
	const positions: Vector3Tuple[] = [];
	const [sx, sy, sz] = serverPosition;

	// Stack jobs vertically behind the server
	for (let i = 0; i < queueLength; i++) {
		positions.push([sx, sy + 0.5 + i * spacing, sz]);
	}

	return positions;
}

/**
 * Calculate position for jobs in PSP.
 */
export function pspJobLayout(
	pspPos: Vector3Tuple,
	jobCount: number,
	maxPerRow: number = 5
): Vector3Tuple[] {
	const positions: Vector3Tuple[] = [];
	const spacing = 0.6;

	for (let i = 0; i < jobCount; i++) {
		const row = Math.floor(i / maxPerRow);
		const col = i % maxPerRow;
		const x = pspPos[0] + (col - (maxPerRow - 1) / 2) * spacing;
		const z = pspPos[2] + row * spacing;
		positions.push([x, pspPos[1] + 0.3, z]);
	}

	return positions;
}

/**
 * Linearly interpolate between two positions.
 */
export function lerp(
	from: Vector3Tuple,
	to: Vector3Tuple,
	t: number
): Vector3Tuple {
	return [
		from[0] + (to[0] - from[0]) * t,
		from[1] + (to[1] - from[1]) * t,
		from[2] + (to[2] - from[2]) * t
	];
}
