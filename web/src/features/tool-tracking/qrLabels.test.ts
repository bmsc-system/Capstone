import { describe, expect, it } from 'vitest';
import { LABEL_SIZES, PAPERS, layoutSheets, perSheet, sheetGrid, sizeHint } from './qrLabels';

describe('QR label sheets', () => {
  const items = ['A', 'B', 'C'];

  it('fills sheets in order and repeats copies', () => {
    const sheets = layoutSheets(items, LABEL_SIZES.large, 3);
    expect(perSheet(LABEL_SIZES.large)).toBe(8);
    expect(sheets).toHaveLength(2);
    expect(sheets[0]).toEqual(['A', 'A', 'A', 'B', 'B', 'B', 'C', 'C']);
    expect(sheets[1]).toEqual(['C']);
  });

  it('leaves the used places of a partly used sheet empty', () => {
    const [first] = layoutSheets(items, LABEL_SIZES.medium, 1, 5);
    expect(first.slice(0, 4)).toEqual([null, null, null, null]);
    expect(first.slice(4)).toEqual(['A', 'B', 'C']);
  });

  it('clamps odd inputs and returns no sheets for no items', () => {
    expect(layoutSheets([], LABEL_SIZES.small)).toEqual([]);
    const [first] = layoutSheets(['A'], LABEL_SIZES.small, 0, 999);
    expect(first.filter(Boolean)).toEqual(['A']);
    expect(first).toHaveLength(perSheet(LABEL_SIZES.small));
  });

  it('every size fits on A4', () => {
    for (const s of Object.values(LABEL_SIZES)) {
      expect(s.left * 2 + s.cols * s.width + (s.cols - 1) * s.colGap).toBeLessThanOrEqual(210.01);
      expect(s.top + s.rows * s.height + (s.rows - 1) * s.rowGap).toBeLessThanOrEqual(297.01);
    }
  });

  it('every size fits on every paper, A4 keeping the sticker-sheet places', () => {
    for (const paper of Object.values(PAPERS)) {
      for (const size of Object.values(LABEL_SIZES)) {
        const g = sheetGrid(size, paper);
        expect(g.left).toBeGreaterThanOrEqual(0);
        expect(g.top).toBeGreaterThanOrEqual(0);
        expect(g.left + g.cols * g.width + (g.cols - 1) * g.colGap).toBeLessThanOrEqual(paper.width + 0.01);
        expect(g.top + g.rows * g.height + (g.rows - 1) * g.rowGap).toBeLessThanOrEqual(paper.height + 0.01);
      }
    }
    expect(sheetGrid(LABEL_SIZES.small, PAPERS.a4)).toBe(LABEL_SIZES.small);
    expect(perSheet(LABEL_SIZES.xsmall)).toBe(65);
  });

  it('fits more labels on Legal than on Letter', () => {
    expect(perSheet(sheetGrid(LABEL_SIZES.small, PAPERS.legal))).toBeGreaterThan(
      perSheet(sheetGrid(LABEL_SIZES.small, PAPERS.letter))
    );
    expect(sizeHint(LABEL_SIZES.small, PAPERS.a4)).toBe('45.7 × 25.4 mm · 40 per A4 sheet');
  });
});
