import { describe, expect, it } from 'vitest';
import {
  ADD_CATALOG_ITEM_LABEL,
  ADD_CONSUMABLE_REMOVED,
  INVENTORY_TAB_OPTIONS,
} from './inventoryTabs';

describe('inventory tabs (Batch 4)', () => {
  it('renames the material catalog tab to Catalog', () => {
    expect(INVENTORY_TAB_OPTIONS.map((t) => t.label)).toEqual([
      'Raw materials',
      'Tools',
      'Consumables',
      'Catalog',
    ]);
    expect(INVENTORY_TAB_OPTIONS.map((t) => t.label as string)).not.toContain('Material catalog');
  });

  it('uses Add catalog item and removes Add consumable', () => {
    expect(ADD_CATALOG_ITEM_LABEL).toBe('Add catalog item');
    expect(ADD_CONSUMABLE_REMOVED).toBe(true);
  });
});
