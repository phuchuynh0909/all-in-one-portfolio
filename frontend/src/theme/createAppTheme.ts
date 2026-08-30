import { createTheme, alpha, type Theme } from '@mui/material/styles';
import type {} from '@mui/x-data-grid/themeAugmentation';
import {
  colorsByMode,
  primitives,
  shadowsByMode,
  componentTokens,
  fontFamily,
  fontSize,
  fontWeight,
  lineHeight,
  letterSpacing,
  radius,
  layout,
  zIndex,
  duration,
  easing,
  type ColorMode,
  type SemanticColors,
} from './tokens';

// --- Type augmentation: expose the domain tokens on the MUI theme ----------

declare module '@mui/material/styles' {
  interface Palette {
    /** Market direction colours. Use these for anything P&L-signed. */
    market: { long: string; longSubtle: string; short: string; shortSubtle: string; flat: string };
    /** Layered surfaces, canvas → overlay. */
    surface: {
      canvas: string;
      default: string;
      raised: string;
      overlay: string;
      inset: string;
      chrome: string;
    };
    /** Border ramp. */
    line: { subtle: string; default: string; strong: string };
    /** Chart-specific colours. */
    chart: { grid: string; axis: string; series: string[] };
  }
  interface PaletteOptions {
    market?: Palette['market'];
    surface?: Palette['surface'];
    line?: Palette['line'];
    chart?: Palette['chart'];
  }
  interface TypographyVariants {
    /** Tabular numerics — prices, quantities, P&L. */
    mono: React.CSSProperties;
    /** Uppercase section eyebrow. */
    overline2: React.CSSProperties;
  }
  interface TypographyVariantsOptions {
    mono?: React.CSSProperties;
    overline2?: React.CSSProperties;
  }
}
declare module '@mui/material/Typography' {
  interface TypographyPropsVariantOverrides {
    mono: true;
    overline2: true;
  }
}

// --------------------------------------------------------------------------

export function createAppTheme(mode: ColorMode): Theme {
  const c: SemanticColors = colorsByMode[mode];
  const shadow = shadowsByMode[mode];
  const ct = componentTokens(c, mode);
  const isDark = mode === 'dark';

  return createTheme({
    palette: {
      mode,
      primary: {
        main: c.accent,
        light: c.accentHover,
        dark: c.accentPressed,
        contrastText: c.textOnAccent,
      },
      secondary: { main: c.info, contrastText: c.textOnAccent },
      success: { main: c.success },
      warning: { main: c.warning },
      error: { main: c.danger },
      info: { main: c.info },
      background: { default: c.bgCanvas, paper: c.bgSurface },
      text: {
        primary: c.textPrimary,
        secondary: c.textSecondary,
        disabled: c.textDisabled,
      },
      divider: c.borderSubtle,
      action: {
        hover: c.bgHover,
        selected: c.bgSelected,
        disabled: c.textDisabled,
        disabledBackground: c.bgHover,
        focus: c.bgActive,
      },
      market: {
        long: c.long,
        longSubtle: c.longSubtle,
        short: c.short,
        shortSubtle: c.shortSubtle,
        flat: c.flat,
      },
      surface: {
        canvas: c.bgCanvas,
        default: c.bgSurface,
        raised: c.bgSurfaceRaised,
        overlay: c.bgSurfaceOverlay,
        inset: c.bgInset,
        chrome: c.bgChrome,
      },
      line: { subtle: c.borderSubtle, default: c.borderDefault, strong: c.borderStrong },
      chart: { grid: c.chartGrid, axis: c.chartAxis, series: c.chartSeries },
    },

    shape: { borderRadius: parseInt(radius.lg, 10) },

    zIndex: {
      appBar: zIndex.appBar,
      drawer: zIndex.drawer,
      modal: zIndex.modal,
      tooltip: zIndex.tooltip,
    },

    typography: {
      fontFamily: fontFamily.sans,
      fontSize: 14,
      htmlFontSize: 16,
      h1: {
        fontSize: fontSize['4xl'],
        fontWeight: fontWeight.bold,
        lineHeight: lineHeight.tight,
        letterSpacing: letterSpacing.tighter,
      },
      h2: {
        fontSize: fontSize['3xl'],
        fontWeight: fontWeight.bold,
        lineHeight: lineHeight.tight,
        letterSpacing: letterSpacing.tighter,
      },
      h3: {
        fontSize: fontSize['2xl'],
        fontWeight: fontWeight.semibold,
        lineHeight: lineHeight.tight,
        letterSpacing: letterSpacing.tight,
      },
      h4: {
        fontSize: fontSize.xl,
        fontWeight: fontWeight.semibold,
        lineHeight: lineHeight.snug,
        letterSpacing: letterSpacing.tight,
      },
      h5: {
        fontSize: fontSize.lg,
        fontWeight: fontWeight.semibold,
        lineHeight: lineHeight.snug,
      },
      h6: {
        fontSize: fontSize.md,
        fontWeight: fontWeight.semibold,
        lineHeight: lineHeight.snug,
      },
      subtitle1: { fontSize: fontSize.base, fontWeight: fontWeight.medium },
      subtitle2: { fontSize: fontSize.sm, fontWeight: fontWeight.semibold },
      body1: { fontSize: fontSize.base, lineHeight: lineHeight.normal },
      body2: { fontSize: fontSize.sm, lineHeight: lineHeight.normal },
      button: {
        fontSize: fontSize.sm,
        fontWeight: fontWeight.medium,
        textTransform: 'none',
        letterSpacing: letterSpacing.normal,
      },
      caption: { fontSize: fontSize.xs, lineHeight: lineHeight.snug, color: c.textTertiary },
      overline: {
        fontSize: fontSize['2xs'],
        fontWeight: fontWeight.semibold,
        textTransform: 'uppercase',
        letterSpacing: letterSpacing.widest,
        lineHeight: lineHeight.snug,
      },
      overline2: {
        fontSize: fontSize['2xs'],
        fontWeight: fontWeight.semibold,
        textTransform: 'uppercase',
        letterSpacing: letterSpacing.widest,
        color: c.textTertiary,
        display: 'block',
      },
      mono: {
        fontFamily: fontFamily.mono,
        fontSize: fontSize.sm,
        fontVariantNumeric: 'tabular-nums',
        letterSpacing: letterSpacing.normal,
      },
    },

    transitions: {
      duration: { shortest: 80, shorter: 120, short: 180, standard: 180, complex: 260 },
      easing: { easeInOut: easing.standard, easeOut: easing.decelerate, easeIn: easing.accelerate },
    },

    components: {
      // ---- Baseline ----
      MuiCssBaseline: {
        styleOverrides: {
          '*, *::before, *::after': { boxSizing: 'border-box' },
          html: { WebkitFontSmoothing: 'antialiased', MozOsxFontSmoothing: 'grayscale' },
          body: {
            backgroundColor: c.bgCanvas,
            color: c.textPrimary,
            fontFeatureSettings: "'cv02', 'cv03', 'cv04', 'cv11'",
          },
          // Numerics stay tabular everywhere so columns line up.
          'code, pre, kbd, samp': { fontFamily: fontFamily.mono },
          '::selection': { background: c.accentSubtle, color: c.textPrimary },
          // Terminal-style thin scrollbars.
          '*::-webkit-scrollbar': { width: 10, height: 10 },
          '*::-webkit-scrollbar-track': { background: ct.scrollbarTrack },
          '*::-webkit-scrollbar-thumb': {
            background: ct.scrollbarThumb,
            borderRadius: radius.full,
            border: `2px solid transparent`,
            backgroundClip: 'content-box',
          },
          '*::-webkit-scrollbar-thumb:hover': { background: c.textTertiary, backgroundClip: 'content-box' },
          '*': { scrollbarWidth: 'thin', scrollbarColor: `${ct.scrollbarThumb} transparent` },
          ':focus-visible': { outline: `2px solid ${c.focusRing}`, outlineOffset: 2 },
        },
      },

      // ---- Surfaces ----
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: c.bgSurface,
            border: `1px solid ${c.borderSubtle}`,
          },
          rounded: { borderRadius: radius.lg },
          outlined: { borderColor: c.borderDefault },
          elevation1: { boxShadow: shadow.sm },
          elevation2: { boxShadow: shadow.md },
          elevation3: { boxShadow: shadow.lg },
          elevation4: { boxShadow: shadow.lg },
          elevation8: { boxShadow: shadow.xl },
          elevation24: { boxShadow: shadow.xl },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: { backgroundColor: c.bgSurface, border: `1px solid ${c.borderSubtle}` },
        },
      },
      MuiAppBar: {
        defaultProps: { elevation: 0, color: 'default' },
        styleOverrides: {
          root: {
            backgroundColor: c.bgChrome,
            backgroundImage: 'none',
            color: c.textPrimary,
            borderBottom: `1px solid ${c.borderSubtle}`,
            boxShadow: 'none',
          },
        },
      },
      MuiToolbar: {
        styleOverrides: {
          root: { minHeight: layout.appBarHeight, '@media (min-width:600px)': { minHeight: layout.appBarHeight } },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: { backgroundColor: c.bgChrome, backgroundImage: 'none' },
          // The divider belongs on whichever edge faces the page content.
          paperAnchorLeft: { borderRight: `1px solid ${c.borderSubtle}` },
          paperAnchorRight: { borderLeft: `1px solid ${c.borderSubtle}` },
          paperAnchorTop: { borderBottom: `1px solid ${c.borderSubtle}` },
          paperAnchorBottom: { borderTop: `1px solid ${c.borderSubtle}` },
        },
      },
      MuiDivider: { styleOverrides: { root: { borderColor: c.borderSubtle } } },

      // ---- Controls ----
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: {
            borderRadius: ct.buttonRadius,
            minHeight: ct.buttonHeight,
            paddingInline: ct.buttonPaddingX,
            fontWeight: fontWeight.medium,
            transition: `background-color ${duration.fast} ${easing.standard}, border-color ${duration.fast} ${easing.standard}`,
          },
          sizeSmall: { minHeight: ct.buttonHeightSm, fontSize: fontSize.xs, paddingInline: 8 },
          sizeLarge: { minHeight: ct.buttonHeightLg, fontSize: fontSize.base },
          containedPrimary: {
            backgroundColor: c.accent,
            color: c.textOnAccent,
            '&:hover': { backgroundColor: c.accentHover },
            '&:active': { backgroundColor: c.accentPressed },
          },
          outlined: {
            borderColor: c.borderDefault,
            color: c.textPrimary,
            '&:hover': { borderColor: c.borderStrong, backgroundColor: c.bgHover },
          },
          text: { color: c.textSecondary, '&:hover': { backgroundColor: c.bgHover, color: c.textPrimary } },
        },
      },
      MuiIconButton: {
        styleOverrides: {
          root: {
            borderRadius: radius.md,
            color: c.textSecondary,
            '&:hover': { backgroundColor: c.bgHover, color: c.textPrimary },
          },
          sizeSmall: { padding: 5 },
        },
      },
      MuiToggleButton: {
        styleOverrides: {
          root: {
            borderRadius: ct.buttonRadius,
            borderColor: c.borderDefault,
            color: c.textSecondary,
            textTransform: 'none',
            fontSize: fontSize.xs,
            paddingInline: 10,
            '&.Mui-selected': {
              backgroundColor: c.accentSubtle,
              color: c.accent,
              borderColor: c.accentBorder,
              '&:hover': { backgroundColor: c.accentSubtle },
            },
          },
        },
      },

      // ---- Inputs ----
      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            backgroundColor: ct.inputBg,
            borderRadius: ct.inputRadius,
            fontSize: fontSize.sm,
            '& .MuiOutlinedInput-notchedOutline': { borderColor: ct.inputBorder },
            '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: ct.inputBorderHover },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderColor: c.accent,
              borderWidth: 1,
              boxShadow: `0 0 0 3px ${alpha(c.accent, 0.16)}`,
            },
          },
          input: { paddingBlock: 7 },
          inputSizeSmall: { paddingBlock: 5 },
        },
      },
      MuiInputLabel: { styleOverrides: { root: { fontSize: fontSize.sm, color: c.textSecondary } } },
      MuiFormHelperText: { styleOverrides: { root: { fontSize: fontSize.xs, marginInline: 2 } } },
      MuiTextField: { defaultProps: { size: 'small', variant: 'outlined' } },
      MuiSelect: { defaultProps: { size: 'small' } },
      MuiMenu: {
        styleOverrides: {
          paper: {
            backgroundColor: c.bgSurfaceOverlay,
            border: `1px solid ${c.borderDefault}`,
            boxShadow: shadow.lg,
          },
        },
      },
      MuiMenuItem: {
        styleOverrides: {
          root: {
            fontSize: fontSize.sm,
            minHeight: 32,
            '&:hover': { backgroundColor: c.bgHover },
            '&.Mui-selected': { backgroundColor: c.bgSelected, '&:hover': { backgroundColor: c.bgSelected } },
          },
        },
      },
      MuiCheckbox: { defaultProps: { size: 'small' }, styleOverrides: { root: { color: c.borderStrong } } },
      MuiRadio: { defaultProps: { size: 'small' } },
      MuiSwitch: { defaultProps: { size: 'small' } },
      MuiSlider: {
        styleOverrides: {
          rail: { backgroundColor: c.borderStrong },
          track: { border: 'none' },
        },
      },

      // ---- Data display ----
      MuiChip: {
        defaultProps: { size: 'small' },
        styleOverrides: {
          root: {
            borderRadius: ct.chipRadius,
            fontSize: fontSize['2xs'],
            fontWeight: fontWeight.medium,
            letterSpacing: letterSpacing.wide,
          },
          sizeSmall: { height: ct.chipHeight },
          outlined: { borderColor: c.borderDefault },
        },
      },
      MuiTooltip: {
        defaultProps: { arrow: true },
        styleOverrides: {
          tooltip: {
            backgroundColor: ct.tooltipBg,
            color: ct.tooltipColor,
            border: `1px solid ${c.borderDefault}`,
            boxShadow: ct.tooltipShadow,
            fontSize: fontSize.xs,
            padding: '6px 8px',
            borderRadius: radius.md,
          },
          arrow: { color: ct.tooltipBg, '&::before': { border: `1px solid ${c.borderDefault}` } },
        },
      },
      MuiTable: { defaultProps: { size: 'small' } },
      MuiTableCell: {
        styleOverrides: {
          root: {
            borderBottomColor: ct.tableBorder,
            fontSize: fontSize.xs,
            padding: '6px 10px',
          },
          head: {
            backgroundColor: ct.tableHeaderBg,
            color: ct.tableHeaderColor,
            fontWeight: fontWeight.semibold,
            fontSize: fontSize['2xs'],
            textTransform: 'uppercase',
            letterSpacing: letterSpacing.wider,
            whiteSpace: 'nowrap',
          },
        },
      },
      MuiTableRow: {
        styleOverrides: {
          root: { '&:hover': { backgroundColor: ct.tableRowHoverBg } },
        },
      },
      MuiDataGrid: {
        styleOverrides: {
          root: {
            border: `1px solid ${c.borderSubtle}`,
            borderRadius: radius.lg,
            fontSize: fontSize.xs,
            '--DataGrid-rowBorderColor': c.borderSubtle,
          },
          columnHeaders: {
            backgroundColor: ct.tableHeaderBg,
            borderBottom: `1px solid ${c.borderDefault}`,
            minHeight: '34px !important',
            maxHeight: '34px !important',
            lineHeight: '34px !important',
          },
          columnHeaderTitle: {
            fontSize: fontSize['2xs'],
            fontWeight: fontWeight.semibold,
            textTransform: 'uppercase',
            letterSpacing: letterSpacing.wider,
            color: ct.tableHeaderColor,
          },
          cell: { borderBottomColor: c.borderSubtle, fontVariantNumeric: 'tabular-nums' },
          row: { '&:hover': { backgroundColor: ct.tableRowHoverBg } },
          footerContainer: { borderTopColor: c.borderSubtle, minHeight: 40 },
          virtualScroller: { backgroundColor: c.bgSurface },
        },
      },
      MuiList: { styleOverrides: { root: { paddingBlock: 4 } } },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: radius.md,
            '&:hover': { backgroundColor: c.bgHover },
            '&.Mui-selected': {
              backgroundColor: c.bgSelected,
              color: c.accent,
              '&:hover': { backgroundColor: c.bgSelected },
            },
          },
        },
      },
      MuiListItemIcon: { styleOverrides: { root: { minWidth: 30, color: 'inherit' } } },
      MuiListItemText: {
        styleOverrides: { primary: { fontSize: fontSize.sm }, secondary: { fontSize: fontSize.xs } },
      },

      // ---- Navigation ----
      MuiTabs: {
        styleOverrides: {
          root: { minHeight: 38, borderBottom: `1px solid ${c.borderSubtle}` },
          indicator: { height: 2, backgroundColor: c.accent },
        },
      },
      MuiTab: {
        styleOverrides: {
          root: {
            minHeight: 38,
            textTransform: 'none',
            fontSize: fontSize.sm,
            fontWeight: fontWeight.medium,
            color: c.textSecondary,
            padding: '0 12px',
            '&:hover': { color: c.textPrimary },
            '&.Mui-selected': { color: c.accent },
          },
        },
      },
      MuiLink: {
        defaultProps: { underline: 'hover' },
        styleOverrides: { root: { color: c.accent, textDecorationColor: c.accentBorder } },
      },
      MuiBreadcrumbs: { styleOverrides: { separator: { color: c.textTertiary } } },

      // ---- Feedback ----
      MuiDialog: {
        styleOverrides: {
          paper: {
            backgroundColor: c.bgSurfaceOverlay,
            border: `1px solid ${c.borderDefault}`,
            boxShadow: shadow.xl,
            backgroundImage: 'none',
          },
        },
      },
      MuiDialogTitle: {
        styleOverrides: {
          root: { fontSize: fontSize.md, fontWeight: fontWeight.semibold, padding: '14px 20px' },
        },
      },
      MuiDialogContent: { styleOverrides: { root: { padding: '0 20px 16px' } } },
      MuiDialogActions: { styleOverrides: { root: { padding: '12px 20px', gap: 8 } } },
      MuiAlert: {
        styleOverrides: {
          root: { borderRadius: radius.md, fontSize: fontSize.sm, border: '1px solid' },
          standardSuccess: { backgroundColor: c.successSubtle, color: c.success, borderColor: alpha(c.success, 0.3) },
          standardError: { backgroundColor: c.dangerSubtle, color: c.danger, borderColor: alpha(c.danger, 0.3) },
          standardWarning: { backgroundColor: c.warningSubtle, color: c.warning, borderColor: alpha(c.warning, 0.3) },
          standardInfo: { backgroundColor: c.infoSubtle, color: c.info, borderColor: alpha(c.info, 0.3) },
        },
      },
      MuiLinearProgress: {
        styleOverrides: {
          root: { height: 3, borderRadius: radius.full, backgroundColor: c.bgInset },
          bar: { borderRadius: radius.full },
        },
      },
      MuiSkeleton: {
        defaultProps: { animation: 'wave' },
        styleOverrides: { root: { backgroundColor: isDark ? c.bgSurfaceRaised : c.bgInset } },
      },
      MuiAccordion: {
        defaultProps: { disableGutters: true, elevation: 0 },
        styleOverrides: {
          root: { border: `1px solid ${c.borderSubtle}`, '&::before': { display: 'none' } },
        },
      },
      MuiBackdrop: {
        styleOverrides: { root: { backgroundColor: alpha(isDark ? primitives.neutral[1000] : primitives.neutral[950], 0.6) } },
      },
    },
  });
}

export default createAppTheme;
