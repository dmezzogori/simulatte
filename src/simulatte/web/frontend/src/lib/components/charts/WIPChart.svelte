<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { TimeSeriesPoint } from '$lib/types/simulation';
	import * as echarts from 'echarts';

	interface Props {
		data: TimeSeriesPoint[];
		color?: string;
	}

	let { data, color = '#00d9ff' }: Props = $props();

	let chartContainer: HTMLDivElement;
	let chart: echarts.ECharts | null = null;

	function updateChart() {
		if (!chart || !data.length) return;

		const option: echarts.EChartsOption = {
			grid: {
				left: 50,
				right: 20,
				top: 20,
				bottom: 30
			},
			xAxis: {
				type: 'value',
				name: 'Time',
				nameLocation: 'center',
				nameGap: 25,
				axisLine: { lineStyle: { color: '#666' } },
				axisLabel: { color: '#999' }
			},
			yAxis: {
				type: 'value',
				axisLine: { lineStyle: { color: '#666' } },
				axisLabel: { color: '#999' },
				splitLine: { lineStyle: { color: '#333' } }
			},
			series: [
				{
					type: 'line',
					data: data.map(p => [p.time, p.value]),
					smooth: true,
					areaStyle: {
						color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
							{ offset: 0, color: color + '80' },
							{ offset: 1, color: color + '10' }
						])
					},
					lineStyle: { color, width: 2 },
					itemStyle: { color }
				}
			],
			tooltip: {
				trigger: 'axis',
				backgroundColor: 'rgba(30, 30, 50, 0.9)',
				borderColor: '#444',
				textStyle: { color: '#eee' },
				formatter: (params: any) => {
					const point = params[0];
					return `Time: ${point.value[0].toFixed(1)}<br/>Value: ${point.value[1].toFixed(2)}`;
				}
			}
		};

		chart.setOption(option);
	}

	onMount(() => {
		chart = echarts.init(chartContainer, 'dark');
		updateChart();

		const resizeObserver = new ResizeObserver(() => {
			chart?.resize();
		});
		resizeObserver.observe(chartContainer);

		return () => {
			resizeObserver.disconnect();
		};
	});

	onDestroy(() => {
		chart?.dispose();
	});

	$effect(() => {
		if (data) updateChart();
	});
</script>

<div class="chart-container" bind:this={chartContainer}></div>

<style>
	.chart-container {
		width: 100%;
		height: 250px;
	}
</style>
