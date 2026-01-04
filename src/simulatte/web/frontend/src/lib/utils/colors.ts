/**
 * Hash a string to a consistent HSL color.
 */
export function skuToColor(sku: string): string {
	let hash = 0;
	for (let i = 0; i < sku.length; i++) {
		hash = ((hash << 5) - hash + sku.charCodeAt(i)) | 0;
	}
	const hue = Math.abs(hash) % 360;
	return `hsl(${hue}, 70%, 50%)`;
}

/**
 * Convert HSL string to hex.
 */
export function hslToHex(hsl: string): number {
	const match = hsl.match(/hsl\((\d+),\s*(\d+)%,\s*(\d+)%\)/);
	if (!match) return 0x888888;

	const h = parseInt(match[1]) / 360;
	const s = parseInt(match[2]) / 100;
	const l = parseInt(match[3]) / 100;

	let r, g, b;
	if (s === 0) {
		r = g = b = l;
	} else {
		const hue2rgb = (p: number, q: number, t: number) => {
			if (t < 0) t += 1;
			if (t > 1) t -= 1;
			if (t < 1 / 6) return p + (q - p) * 6 * t;
			if (t < 1 / 2) return q;
			if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
			return p;
		};
		const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
		const p = 2 * l - q;
		r = hue2rgb(p, q, h + 1 / 3);
		g = hue2rgb(p, q, h);
		b = hue2rgb(p, q, h - 1 / 3);
	}

	return (
		(Math.round(r * 255) << 16) |
		(Math.round(g * 255) << 8) |
		Math.round(b * 255)
	);
}

/**
 * Get urgency color gradient.
 * urgency: 0 (normal/green) to 1 (critical/red)
 */
export function urgencyToColor(urgency: number): number {
	// Clamp urgency to 0-1
	const u = Math.max(0, Math.min(1, urgency));

	// Interpolate from green (0x33cc66) through yellow (0xcccc33) to red (0xcc3333)
	if (u < 0.5) {
		// Green to yellow
		const t = u * 2;
		const r = Math.round(0x33 + (0xcc - 0x33) * t);
		const g = Math.round(0xcc - (0xcc - 0xcc) * t);
		const b = Math.round(0x66 + (0x33 - 0x66) * t);
		return (r << 16) | (g << 8) | b;
	} else {
		// Yellow to red
		const t = (u - 0.5) * 2;
		const r = 0xcc;
		const g = Math.round(0xcc + (0x33 - 0xcc) * t);
		const b = 0x33;
		return (r << 16) | (g << 8) | b;
	}
}

/**
 * Blend two colors based on urgency.
 */
export function blendWithUrgency(baseColor: number, urgency: number): number {
	if (urgency < 0.3) return baseColor;

	const urgencyColor = urgencyToColor(urgency);
	const blendFactor = (urgency - 0.3) / 0.7;

	const br = (baseColor >> 16) & 0xff;
	const bg = (baseColor >> 8) & 0xff;
	const bb = baseColor & 0xff;

	const ur = (urgencyColor >> 16) & 0xff;
	const ug = (urgencyColor >> 8) & 0xff;
	const ub = urgencyColor & 0xff;

	const r = Math.round(br + (ur - br) * blendFactor);
	const g = Math.round(bg + (ug - bg) * blendFactor);
	const b = Math.round(bb + (ub - bb) * blendFactor);

	return (r << 16) | (g << 8) | b;
}
