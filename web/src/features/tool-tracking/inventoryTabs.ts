/** Inventory page tab labels — kept here so vitest can assert Batch 4 copy. */
export const INVENTORY_TAB_OPTIONS = [
  { label: 'Raw materials', value: 'raw-materials' },
  { label: 'Tools', value: 'tools' },
  { label: 'Consumables', value: 'consumables' },
  { label: 'Catalog', value: 'catalog' },
] as const;

export const ADD_CATALOG_ITEM_LABEL = 'Add catalog item';

/** Consumables tab no longer creates stock; Catalog does. */
export const ADD_CONSUMABLE_REMOVED = true;
