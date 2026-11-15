import apiClient from './client';

export type CatalogKind = 'MATERIAL' | 'CONSUMABLE';
export type CatalogTypeFilter = 'all' | 'MATERIAL' | 'CONSUMABLE';

export interface CatalogItem {
  id: string;
  kind: CatalogKind;
  name: string;
  shopTerm: string | null;
  code: string | null;
  gradesOrSizes: string[];
  grades?: string[];
  sizeSpec?: string | null;
  unit: string;
  defaultUnit?: string;
  category: string | null;
  active: boolean;
  notStockedYet: boolean | null;
  /** Consumable offered on a supplier purchase order (most are bought at a store). */
  orderedFromSupplier?: boolean;
  quantityOnHand?: number | null;
  minimumStock?: number | null;
}

export interface CatalogList {
  items: CatalogItem[];
  materialUnits: string[];
  consumableUnits: string[];
  categories: string[];
}

export type CatalogCreateInput = {
  kind: CatalogKind;
  name: string;
  shopTerm?: string | null;
  grades?: string[];
  gradesOrSizes?: string[];
  defaultUnit?: string;
  unit?: string;
  category?: string | null;
  sizeSpec?: string | null;
  active?: boolean;
  orderedFromSupplier?: boolean;
};

export type CatalogUpdateInput = Partial<
  Omit<CatalogCreateInput, 'kind'> & { active?: boolean; minimumStock?: number | null }
>;

export const MATERIAL_UNITS = ['pcs', 'm', 'ft', 'sheet'];
export const CONSUMABLE_UNITS = [
  'pcs',
  'pack',
  'roll',
  'box',
  'spool',
  'can',
  'pail',
  'bottle',
  'pair',
  'gallon',
  'liter',
];

export const catalogApi = {
  list: (params?: {
    search?: string;
    type?: CatalogTypeFilter;
    includeInactive?: boolean;
  }) =>
    apiClient.get<CatalogList>('/catalog', {
      params: {
        search: params?.search || undefined,
        type: params?.type && params.type !== 'all' ? params.type : undefined,
        includeInactive: params?.includeInactive ? 'true' : undefined,
      },
    }),
  create: (data: CatalogCreateInput) => apiClient.post<CatalogItem>('/catalog', data),
  updateMaterial: (id: string, data: CatalogUpdateInput) =>
    apiClient.patch<CatalogItem>(`/catalog/materials/${id}`, data),
  updateConsumable: (id: string, data: CatalogUpdateInput) =>
    apiClient.patch<CatalogItem>(`/catalog/consumables/${id}`, data),
};

/** Matches name, shop term, code, category, or any grade/size. */
export function catalogMatches(item: CatalogItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const grades = item.gradesOrSizes?.length
    ? item.gradesOrSizes
    : item.grades || (item.sizeSpec ? [item.sizeSpec] : []);
  return [item.name, item.shopTerm, item.code, item.category, ...grades]
    .filter(Boolean)
    .some((v) => String(v).toLowerCase().includes(q));
}
