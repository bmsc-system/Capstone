import { useMemo, useState } from 'react';
import { Segmented } from 'antd';
import { BookOpen, Layers, Package, Wrench } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import ToolsAssetsPanel from './ToolsAssetsPanel';
import RawMaterialsPanel from './RawMaterialsPanel';
import MaterialCatalogPanel from './MaterialCatalogPanel';
import ConsumablesPanel from './ConsumablesPanel';
import { INVENTORY_TAB_OPTIONS } from './inventoryTabs';

type PageTab = 'tools' | 'consumables' | 'raw-materials' | 'catalog';

const TAB_ICON: Record<PageTab, typeof Wrench> = {
  tools: Wrench,
  consumables: Package,
  'raw-materials': Layers,
  catalog: BookOpen,
};

/** Inventory: raw materials bought for jobs, tools (borrowed by QR),
 *  consumables (what the shop buys), and the catalog of what can be ordered. */
export default function ToolsPage() {
  const { canOfficeWrite } = useAuth();
  const [searchParams] = useSearchParams();
  // ?tab=consumables (or raw-materials, catalog) opens that tab, e.g. from Analytics.
  const [tab, setTab] = useState<PageTab>(() => {
    const asked = searchParams.get('tab');
    return INVENTORY_TAB_OPTIONS.some((o) => o.value === asked) ? (asked as PageTab) : INVENTORY_TAB_OPTIONS[0].value;
  });

  const tabOptions = useMemo(
    () =>
      INVENTORY_TAB_OPTIONS.map((t) => {
        const Icon = TAB_ICON[t.value];
        return {
          value: t.value,
          label: (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Icon size={14} strokeWidth={2} aria-hidden />
              {t.label}
            </span>
          ),
        };
      }),
    []
  );

  return (
    <div>
      <div className="admin-h-scroll">
        <Segmented
          style={{ marginBottom: 16 }}
          value={tab}
          onChange={(v) => setTab(v as PageTab)}
          options={tabOptions}
        />
      </div>

      {tab === 'raw-materials' && <RawMaterialsPanel />}
      {tab === 'tools' && <ToolsAssetsPanel />}
      {tab === 'consumables' && <ConsumablesPanel canEdit={canOfficeWrite} />}
      {tab === 'catalog' && <MaterialCatalogPanel canEdit={canOfficeWrite} />}
    </div>
  );
}
