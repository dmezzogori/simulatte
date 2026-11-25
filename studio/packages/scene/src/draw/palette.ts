// The colours of the map (spec section 5.4, goal 7). Every colour the map draws comes from here.

export const PALETTE = {
  /** The canvas and its grid: the scene's colours before the map. */
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
  /** Vehicle states (spec section 5.4); drawn from Task 5 on. */
  moving: 0x2563eb,
  waiting: 0xd97706,
  idle: 0x94a3b8,
  charging: 0x16a34a,
  stranded: 0xdc2626,
  load: 0x92400e,
  /** Held floor and wait lines (spec section 5.5); drawn from Task 6 on. */
  held: 0x2563eb,
  waitLine: 0xd97706,
  /** Facilities (spec section 5.4); drawn from Task 5 on. */
  warehouse: 0xfde68a,
  warehouseShelf: 0xd9a520,
  warehouseOutline: 0xb45309,
  parking: 0x15803d,
  chargingStation: 0xdcfce7,
  chargingOutline: 0x15803d,
  /** The light outline every sprite carries. */
  outline: 0xffffff,
  /** Reserved for the facilities of a later release; nothing draws them yet. */
  rack: 0xcbd5e1,
  staging: 0x475569,
  dock: 0x475569,
} as const;
