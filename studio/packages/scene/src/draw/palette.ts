// The colours of the map (spec section 5.4, goal 7). Every colour the map draws comes from here.

export const PALETTE = {
  /** The canvas and the map's grid: the scene's colours before the map (the path without a map keeps its literals). */
  background: 0xf4f5f0,
  grid: 0xe7eae3,
  /** A lane of the floor. */
  strip: 0xcbd5e1,
  /** The direction marks on a lane. */
  chevron: 0x64748b,
  /** A node of the network at true-size level: fill, outline and id. */
  node: 0xffffff,
  nodeOutline: 0x64748b,
  nodeText: 0x475569,
  /** The outline of the selected element. */
  selected: 0x315b4b,
  /** A port of a placed entity. */
  port: 0x52695c,
  /** The scale bar and its length. */
  scaleBar: 0x0f172a,
  /** Labels of glyphs. */
  text: 0x303d36,
  /** The decorative trails of jobs moving between equipment. */
  trail: 0x668471,
  /** Vehicle states (spec section 5.4). */
  moving: 0x2563eb,
  waiting: 0xd97706,
  idle: 0x94a3b8,
  charging: 0x16a34a,
  stranded: 0xdc2626,
  load: 0x92400e,
  /** The short label of a vehicle, darker shades of its state colour so that text stays legible. */
  movingText: 0x1d4ed8,
  waitingText: 0xb45309,
  idleText: 0x475569,
  chargingText: 0x15803d,
  strandedText: 0xb91c1c,
  /** Fleet stripes, in fleet id order: yellow, magenta, navy, black, light cyan, pink; each contrasts with every state colour. */
  fleets: [0xfacc15, 0xc026d3, 0x1e1b4b, 0x0a0a0a, 0x67e8f9, 0xf9a8d4],
  /** Held floor and wait lines (spec section 5.5), translucent over the floor: plain, and held by the selection. */
  held: 0x2563eb,
  heldAlpha: 0.3,
  heldSelectedAlpha: 0.6,
  waitLine: 0xd97706,
  /** The selected vehicle's route ahead: the moving blue in a light casing. */
  route: 0x2563eb,
  /** The origin-to-destination arrow of an order (M12). */
  flow: 0x1e293b,
  /** Facilities (spec section 5.4). */
  warehouse: 0xfde68a,
  warehouseShelf: 0xd9a520,
  warehouseOutline: 0xb45309,
  parking: 0x15803d,
  chargingStation: 0xdcfce7,
  chargingOutline: 0x15803d,
  /** The inside of a parking area at true-size level, and of a warehouse's bay. */
  bay: 0xffffff,
  /** The count of orders waiting at a warehouse: a white number in this circle. */
  badge: 0xb45309,
  badgeText: 0xffffff,
  /** The light outline every sprite carries, and the marks on a vehicle's body. */
  outline: 0xffffff,
  /** Reserved for the facilities of a later release; nothing draws them yet. */
  rack: 0xcbd5e1,
  staging: 0x475569,
  dock: 0x475569,
} as const;
