import type { ThemeConfig } from 'antd';
import { colors, font, layout, radius, shadow, tones } from './tokens';

const baseToken: NonNullable<ThemeConfig['token']> = {
  colorPrimary: colors.primary,
  colorInfo: colors.primary,
  colorLink: colors.primary,
  colorSuccess: tones.green.solid,
  colorWarning: tones.amber.solid,
  colorError: tones.red.solid,
  colorBgLayout: colors.page,
  colorBgContainer: colors.surface,
  colorBorder: colors.border,
  colorBorderSecondary: colors.border,
  colorSplit: colors.border,
  colorText: colors.text,
  colorTextHeading: colors.text,
  colorTextSecondary: colors.textSecondary,
  colorTextTertiary: colors.textMuted,
  colorTextPlaceholder: colors.textDisabled,
  colorTextDisabled: colors.textMuted,
  colorBgContainerDisabled: colors.surfaceMuted,
  colorFillAlter: colors.surfaceMuted,
  colorBgMask: colors.overlay,
  fontFamily: font.family,
  fontFamilyCode: font.mono,
  fontSize: font.size.base,
  fontSizeSM: font.size.xs,
  fontWeightStrong: font.weight.semibold,
  borderRadius: radius.md,
  borderRadiusSM: radius.sm,
  borderRadiusLG: radius.lg,
  boxShadow: shadow.raised,
  boxShadowSecondary: shadow.overlay,
  controlHeight: layout.controlHeight,
  controlOutline: 'rgba(37, 99, 235, 0.28)',
  controlOutlineWidth: 2,
  lineWidthFocus: 2,
  motionDurationFast: '0.1s',
  motionDurationMid: '0.15s',
};

const components: ThemeConfig['components'] = {
  Layout: {
    siderBg: colors.navy,
    headerBg: colors.surface,
    bodyBg: colors.page,
  },
  Menu: {
    darkItemBg: colors.navy,
    darkSubMenuItemBg: colors.navy,
    darkItemColor: colors.sidebarText,
    darkItemHoverBg: 'rgba(148, 163, 184, 0.12)',
    darkItemHoverColor: colors.textOnColor,
    darkItemSelectedBg: colors.sidebarActiveBg,
    darkItemSelectedColor: colors.textOnColor,
    darkGroupTitleColor: colors.sidebarMuted,
    itemBorderRadius: radius.md,
    itemHeight: 36,
    itemMarginInline: 8,
    itemMarginBlock: 1,
    iconSize: 16,
    collapsedIconSize: 18,
    collapsedWidth: layout.sidebarCollapsedWidth,
    groupTitleFontSize: 11,
  },
  Segmented: {
    trackBg: 'transparent',
    trackPadding: 0,
    itemColor: colors.textSecondary,
    itemHoverColor: colors.text,
    itemHoverBg: colors.surfaceSunken,
    itemActiveBg: colors.border,
    itemSelectedBg: colors.primary,
    itemSelectedColor: colors.textOnColor,
    borderRadius: radius.pill,
    borderRadiusSM: radius.pill,
  },
  Tabs: {
    itemColor: colors.textSecondary,
    itemSelectedColor: colors.primary,
    inkBarColor: colors.primary,
    horizontalMargin: '0 0 16px 0',
  },
  Table: {
    headerBg: colors.surfaceMuted,
    headerColor: colors.textMuted,
    headerSplitColor: 'transparent',
    headerSortActiveBg: colors.surfaceSunken,
    headerSortHoverBg: colors.surfaceSunken,
    rowHoverBg: colors.surfaceMuted,
    borderColor: colors.border,
    cellPaddingBlock: 11,
    cellPaddingInline: 12,
    cellPaddingBlockMD: 11,
    cellPaddingInlineMD: 12,
    cellPaddingBlockSM: 6,
    cellPaddingInlineSM: 8,
    cellFontSize: font.size.base,
    cellFontSizeMD: font.size.base,
    headerBorderRadius: radius.md,
  },
  Button: {
    fontWeight: font.weight.semibold,
    primaryShadow: 'none',
    defaultShadow: 'none',
    dangerShadow: 'none',
    defaultBorderColor: colors.borderStrong,
    defaultColor: colors.text,
  },
  Card: {
    colorBorderSecondary: colors.border,
    borderRadiusLG: radius.lg,
    bodyPadding: layout.cardPadding,
    headerPadding: layout.cardPadding,
    headerFontSize: font.size.md,
    headerHeight: 40,
  },
  Modal: {
    borderRadiusLG: radius.lg,
    titleFontSize: font.size.lg,
  },
  Drawer: {
    footerPaddingBlock: 12,
    footerPaddingInline: 16,
  },
  Form: {
    labelColor: colors.textSecondary,
    labelFontSize: font.size.sm,
    verticalLabelPadding: '0 0 4px',
    itemMarginBottom: 12,
  },
  Tooltip: {
    colorBgSpotlight: colors.navy,
  },
  Skeleton: {
    gradientFromColor: colors.surfaceSunken,
    gradientToColor: colors.border,
  },
  Tag: {
    defaultBg: colors.surfaceSunken,
    defaultColor: colors.textSecondary,
  },
  Pagination: {
    itemSize: 28,
  },
  /* One compact alert: 16px icon, 14px title, clean tints from the tone palette
     (antd would derive a grey-brown error fill from the maroon). */
  Alert: {
    defaultPadding: '8px 12px',
    withDescriptionPadding: '10px 14px',
    withDescriptionIconSize: 16,
    fontSizeLG: font.size.base,
    colorInfoBg: tones.blue.bg,
    colorInfoBorder: tones.blue.border,
    colorWarning: '#d97706',
    colorWarningBg: tones.amber.bg,
    colorWarningBorder: tones.amber.border,
    colorErrorBg: tones.red.bg,
    colorErrorBorder: tones.red.border,
    colorSuccessBg: tones.green.bg,
    colorSuccessBorder: tones.green.border,
  },
};

/** Office screens (Admin, Office Staff). */
export const officeTheme: ThemeConfig = { token: baseToken, components };

/** Office screens on phones (≤768): same look, 40px tap targets. */
export const officeTouchTheme: ThemeConfig = {
  token: {
    ...baseToken,
    controlHeight: layout.touchTarget,
    controlHeightSM: layout.touchTarget,
    controlHeightLG: layout.workerTapTarget,
  },
  components: {
    ...components,
    Menu: { ...components.Menu, itemHeight: layout.touchTarget },
    Pagination: { itemSize: layout.touchTarget, itemSizeSM: layout.touchTarget },
    DatePicker: { cellHeight: 36, cellWidth: 40, withoutTimeCellHeight: 48 },
  },
};

/**
 * Office shell below 1024px, nested inside officeTheme / officeTouchTheme.
 * One 36px control height; small controls stay ≥30px with spacing around them.
 */
export const officeCompactTheme: ThemeConfig = {
  token: {
    controlHeight: 36,
    controlHeightSM: 30,
    controlHeightLG: 40,
    fontSizeIcon: 13,
  },
  components: {
    Button: {
      paddingInline: 12,
      paddingInlineSM: 8,
      contentFontSize: font.size.base,
      contentFontSizeSM: font.size.sm,
      onlyIconSize: 16,
      onlyIconSizeSM: 14,
    },
    Input: { paddingInline: 10 },
    Select: { optionHeight: 36 },
    Menu: { ...components.Menu, itemHeight: 44 },
    Pagination: { itemSize: 32, itemSizeSM: 28 },
    DatePicker: { cellHeight: 34, cellWidth: 38, withoutTimeCellHeight: 44 },
  },
};

/** Production worker phone screens: same colours, shop-floor tap targets. */
export const workerTheme: ThemeConfig = {
  token: {
    ...baseToken,
    borderRadius: radius.lg,
    controlHeight: layout.workerTapTarget,
    controlHeightLG: 52,
    controlHeightSM: layout.workerTapTarget,
  },
  components: {
    ...components,
    Segmented: { ...components.Segmented, borderRadius: radius.pill },
  },
};
